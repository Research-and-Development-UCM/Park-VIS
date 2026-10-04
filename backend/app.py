import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

from .logging_config import setup_logging, setup_otel, vulture_logger as logger
from .config import config
import sys
import threading
import traceback

# Initialize logging and tracing BEFORE other imports to catch everything
setup_logging()
setup_otel()

from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx
import asyncio
import uuid
import json
import cv2
import time
import numpy as np
from typing import List, Optional
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from . import models, schemas, auth, inference, scheduler, crud, database
from .stream_manager import stream_manager
import signal
import datetime
from opentelemetry import trace

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if config.PROFILE_MEMORY:
        import tracemalloc
        tracemalloc.start(10) # Track up to 10 stack frames
        logger.info("Application starting up (Memory tracing ENABLED - High Overhead)...")
    else:
        logger.info("Application starting up (Memory tracing disabled)...")

    # C1 audit fix: warn when JWT signing key didn't come from an
    # explicit env var. File-backed and auto-generated keys are
    # functional but operators should set PARK_VIS_SECRET_KEY for
    # production so the key is portable across hosts (containers,
    # restores from backup, etc.) and so the install is reproducible.
    if config.SECRET_KEY_SOURCE == "env":
        logger.info("JWT signing key loaded from PARK_VIS_SECRET_KEY env var.")
    else:
        key_path = os.path.join(config.LV_HOME, "secret_key")
        logger.warning(
            "JWT signing key loaded from {source} file at {path} — "
            "PARK_VIS_SECRET_KEY env var is NOT set. This is fine for "
            "single-host installs, but the key won't be portable across "
            "containers or restored from backup. Set PARK_VIS_SECRET_KEY "
            "in production deployments.",
            source=config.SECRET_KEY_SOURCE,
            path=key_path,
        )
    
    # Initialize database tables and seed default data
    from . import init_db
    init_db.main()
    
    scheduler.start_background()
    from .alerts import engine as alerts_engine, dispatcher as alerts_dispatcher
    alerts_engine.start_background()
    alerts_dispatcher.start_background()
    yield
    # Shutdown
    logger.info("Application shutting down, cleaning up...")
    scheduler.stop_background()
    alerts_engine.stop_background()
    alerts_dispatcher.stop_background()
    stream_manager.stop_all()
    # Give a tiny bit of time for tasks to finish
    if "PYTEST_CURRENT_TEST" not in os.environ:
        await asyncio.sleep(0.5)
    
    import tracemalloc
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    logger.info("Cleanup complete.")

app = FastAPI(lifespan=lifespan)
# OTel app instrumentation happens inside setup_otel if app is passed, 
# but for FastAPI it's often better to do it here if we want the middleware order correct.
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
FastAPIInstrumentor.instrument_app(app)

# --- Immediate Signal Handling to break shutdown deadlocks ---
def dump_stacks(*args):
    import traceback, sys, threading
    logger.info("--- [THREAD STACK DUMP] ---")
    id2name = {t.ident: t.name for t in threading.enumerate()}
    for threadId, stack in sys._current_frames().items():
        name = id2name.get(threadId, "Unknown")
        stack_str = "".join(traceback.format_stack(stack))
        logger.debug("Thread: {name} (ID: {tid})\n{stack}", name=name, tid=threadId, stack=stack_str)

# --- Immediate Signal Handling to break shutdown deadlocks ---
_exit_initiated = False
def trigger_immediate_stop(*args):
    global _exit_initiated
    if _exit_initiated:
        logger.warning("Second exit signal received. Force killing now.")
        os._exit(1)
    
    _exit_initiated = True
    logger.info("Received exit signal, triggering immediate background cleanup...")
    
    try:
        # 1. Signal background tasks to stop
        scheduler.stop_background()
        from .alerts import engine as alerts_engine, dispatcher as alerts_dispatcher
        alerts_engine.stop_background()
        alerts_dispatcher.stop_background()
        stream_manager.stop_all()
        
        # 2. Launch a watchdog thread to ensure we actually die
        def panic_exit():
            import time
            time.sleep(5.0)
            logger.error("Exit watchdog triggered (5s limit). Force terminating process group.")
            if hasattr(os, "killpg"):
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                except OSError:
                    pass
            os._exit(1)

        import threading
        threading.Thread(target=panic_exit, daemon=True, name="App-PanicExitTimer").start()

        # 3. Try a normal exit
        logger.info("Cleanup initiated. Waiting up to 5s for threads...")
    except Exception as e:
        logger.error("Cleanup error: {e}", e=e)
        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            except OSError:
                pass
        os._exit(1)

# Register handlers for both main and child processes
signal.signal(signal.SIGINT, trigger_immediate_stop)
signal.signal(signal.SIGTERM, trigger_immediate_stop)
if hasattr(signal, "SIGUSR1"):
    signal.signal(signal.SIGUSR1, dump_stacks)
# -------------------------------------------------------------

# Allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# C5 audit fix: log a one-time loud WARNING at startup noting that
# ``allow_origins=["*"]`` combined with ``allow_credentials=True`` is
# rejected by the Fetch spec (browsers refuse the response) and is
# unsafe if a browser ever does honor it.  Production deployments
# MUST set ``PARK_VIS_CORS_ALLOW_ORIGINS`` to a comma-separated
# allowlist.  We don't change the default here (the audit keeps the
# current permissive behavior for dev installs) — we just make sure
# the operator sees the warning.
logger.warning(
    "CORS configured with allow_origins=['*'] and allow_credentials=True. "
    "Browsers reject this combination per the Fetch spec, so credentials "
    "will NOT be exchanged cross-origin in practice — but if a browser "
    "ever does honor it, any origin can read the response. For production "
    "set PARK_VIS_CORS_ALLOW_ORIGINS to a comma-separated allowlist of "
    "trusted origins (e.g. 'https://parking.example.com'). See C5 in "
    "the security audit.",
)

# Dependency

def get_db():
    yield from database.get_db()

def require_auth(user: models.User = Depends(auth.get_auth_user)):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user

def optional_auth(user: models.User = Depends(auth.get_auth_user)):
    return user

def get_user_perms(user: models.User = Depends(require_auth)):
    if user.is_admin:
        return auth.ADMIN_PERMISSIONS
    return auth.parse_permissions(user)

def has_permission(perm: str, perms: List[str]):
    if perm not in perms:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {perm}")
    return True

# Auth
@app.post("/api/login")
async def login(form: schemas.LoginForm, db: Session = Depends(get_db)):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("login_handler"):
        # C3 audit fix: on a fresh install, no admin user exists.
        # Instead of returning a generic "invalid credentials" error
        # (which would mask the setup state from the operator), we
        # return a 428 Precondition Required with a structured body
        # the frontend uses to redirect to /setup.
        admin_exists = (
            db.query(models.User)
            .filter(models.User.is_admin == True)
            .first()
        )
        if admin_exists is None:
            raise HTTPException(
                status_code=428,
                detail={
                    "code": "setup_required",
                    "message": (
                        "No administrator user exists. Visit /setup to "
                        "create the first admin account before logging in."
                    ),
                },
            )
        result = auth.login_user(form, db)
        if not result:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        return result


# C3 audit fix: first-load admin setup workflow.
#
# On a fresh install there is no default admin.  The operator must
# call POST /api/setup/admin once to create the first administrator;
# the frontend redirects to /setup automatically on first load.
#
# Once any admin exists, the endpoint returns 409 Conflict so a
# malicious caller cannot reset the admin or create a second one
# via this unauthenticated path.
@app.get("/api/setup/status")
async def setup_status(db: Session = Depends(get_db)):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("setup_status"):
        any_user = db.query(models.User).first()
        any_admin = (
            db.query(models.User)
            .filter(models.User.is_admin == True)
            .first()
        )
        return schemas.SetupStatus(
            setup_required=any_admin is None,
            has_any_user=any_user is not None,
        )


@app.post("/api/setup/admin", response_model=schemas.UserOut)
async def setup_create_first_admin(
    body: schemas.FirstAdminCreate,
    db: Session = Depends(get_db),
):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("setup_create_first_admin"):
        # Refuse to create a second admin via the unauthenticated
        # setup path.  Once any admin exists, the operator must use
        # the authenticated /api/users endpoint to create more users.
        any_admin = (
            db.query(models.User)
            .filter(models.User.is_admin == True)
            .first()
        )
        if any_admin is not None:
            raise HTTPException(
                status_code=409,
                detail="An administrator already exists. Use the Users page to add more accounts.",
            )

        # Username uniqueness check
        if db.query(models.User).filter_by(username=body.username).first():
            raise HTTPException(
                status_code=409,
                detail=f"Username '{body.username}' is already taken.",
            )

        # Password strength: a freshly-set admin password should not
        # be trivial.  Enforce a minimum length here (additional
        # complexity rules can be added later).
        if len(body.password) < 6:
            raise HTTPException(
                status_code=400,
                detail="Password must be at least 6 characters.",
            )

        from .auth import hash_password
        admin = models.User(
            username=body.username,
            password_hash=hash_password(body.password),
            is_admin=True,
            permissions="[]",
        )

        db.add(admin)

        db.commit()
        db.refresh(admin)
        logger.info(
            "First administrator '{user}' created via /api/setup/admin.",
            user=admin.username,
        )
        # UserOut expects permissions as List[str], not the JSON string
        # stored in the column — convert via the same helper crud.get_users
        # uses.  Avoids a ResponseValidationError.
        return crud._user_with_list_permissions(admin)

# API Keys
@app.get("/api/api_keys", response_model=List[schemas.APIKeyOut])
async def list_api_keys(db: Session = Depends(get_db), user: models.User = Depends(require_auth), perms: List[str] = Depends(get_user_perms)):
    has_permission("manage_api_keys", perms)
    keys = crud.get_api_keys(db)
    # Manual mapping to include creator_username
    results = []
    for k in keys:
        res = schemas.APIKeyOut.model_validate(k)
        if k.creator:
            res.creator_username = k.creator.username
        results.append(res)
    return results

@app.post("/api/api_keys", response_model=schemas.APIKeyGenerated)
async def create_api_key(key_data: schemas.APIKeyCreate, db: Session = Depends(get_db), user: models.User = Depends(require_auth), perms: List[str] = Depends(get_user_perms)):
    has_permission("manage_api_keys", perms)
    db_key = crud.create_api_key(db, user.id, key_data.name)
    res = schemas.APIKeyGenerated.model_validate(db_key)
    res.full_key = getattr(db_key, 'full_key')
    if db_key.creator:
        res.creator_username = db_key.creator.username
    return res

@app.delete("/api/api_keys/{key_id}")
async def delete_api_key(key_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth), perms: List[str] = Depends(get_user_perms)):
    has_permission("manage_api_keys", perms)
    if not crud.delete_api_key(db, key_id):
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": "Key deleted"}

@app.post("/api/change-password")
async def change_password(form: schemas.PasswordChange, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    from .auth import verify_password, hash_password
    if not user.password_hash or not verify_password(form.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect old password")

    user.password_hash = hash_password(form.new_password)
    db.commit()
    return {"message": "Password updated successfully"}


# User Management
@app.get("/api/users/me", response_model=schemas.UserCurrent)
async def get_me(user: models.User = Depends(require_auth)):
    # Permissions are stored as JSON string in DB, schema expects list
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "permissions": auth.parse_permissions(user)
    }

@app.get("/api/users", response_model=List[schemas.UserOut])
async def list_users(db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_users"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_users")
    return crud.get_users(db)

@app.post("/api/users", response_model=schemas.UserOut)
async def create_user(user_data: schemas.UserCreate, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_users"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_users")
    return crud.create_user(user_data, db)

@app.put("/api/users/{user_id}", response_model=schemas.UserOut)
async def update_user(user_id: int, user_update: schemas.UserUpdate, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_users"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_users")
    try:
        result = crud.update_user(db, user_id, user_update)
        if not result:
            raise HTTPException(status_code=404, detail="User not found")
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_users"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_users")
    try:
        if not crud.delete_user(db, user_id):
            raise HTTPException(status_code=404, detail="User not found")
        return {"message": "User deleted"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Cameras
@app.get("/api/cameras", response_model=List[schemas.CameraOutPublic])
async def list_cameras(db: Session = Depends(get_db), user: models.User = Depends(optional_auth)):
    """List every camera, including static-image test cameras.

    Earlier versions hid ``is_test`` cameras from non-admin users, which
    made the Cameras & Spaces page silently disagree with the Live
    Dashboard (which always showed all cameras). The two pages now show
    the same set — admin-only filtering happens at the route level
    (you need ``manage_cameras`` to add/edit/delete cameras, but
    *seeing* them is unrestricted).

    C4 audit fix: returns ``CameraOutPublic`` (no ``stream_password``)
    so RTSP credentials don't leak to any authenticated user.  Admin
    routes that need the password continue to use the full ``CameraOut``.
    """
    return db.query(models.Camera).order_by(models.Camera.name.asc()).all()

@app.post("/api/cameras", response_model=schemas.CameraOut)
async def create_camera(cam: schemas.CameraCreate, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_cameras")
    res = crud.create_camera(cam, db)
    scheduler.trigger_inference()
    return res

@app.put("/api/cameras/{camera_id}")
async def update_camera(camera_id: int, cam: schemas.CameraCreate, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_cameras")
    result = crud.update_camera(db, camera_id, cam)
    if not result:
        raise HTTPException(status_code=404, detail="Camera not found")
    scheduler.trigger_inference()
    return result

@app.delete("/api/cameras/{camera_id}")
async def delete_camera(camera_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_cameras")
    if not crud.delete_camera(db, camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"message": "Camera deleted"}

@app.get("/api/cameras/{camera_id}/recent-scans")
async def get_recent_scans(camera_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    scans = db.query(models.CameraScan.id, models.CameraScan.timestamp)\
              .filter(models.CameraScan.camera_id == camera_id, models.CameraScan.has_image == True)\
              .order_by(models.CameraScan.timestamp.desc())\
              .limit(20).all()
    return [{"id": s.id, "timestamp": s.timestamp.isoformat() + "Z"} for s in scans]

@app.get("/api/cameras/{camera_id}/scan-at-time")
async def get_scan_at_time(camera_id: int, target_time: str, direction: Optional[str] = Query(None), exclude_id: Optional[int] = Query(None), db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    # Parse target time ensuring it has zone info
    try:
        dt = datetime.datetime.fromisoformat(target_time.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format")
    
    # SQLite stores naive (UTC) - strip tz for accurate comparison
    dt_naive = dt.replace(tzinfo=None)
    
    query = db.query(models.CameraScan.id, models.CameraScan.timestamp)\
              .filter(models.CameraScan.camera_id == camera_id, models.CameraScan.has_image == True)
    
    if exclude_id:
        query = query.filter(models.CameraScan.id != exclude_id)

    # Directional filtering to prevent getting stuck in same-time loops
    if direction == "older":
        query = query.filter(models.CameraScan.timestamp <= dt_naive).order_by(models.CameraScan.timestamp.desc())
    elif direction == "newer":
        query = query.filter(models.CameraScan.timestamp >= dt_naive).order_by(models.CameraScan.timestamp.asc())
    else:
        # Default closest-point logic
        query = query.order_by(func.abs(func.julianday(models.CameraScan.timestamp) - func.julianday(dt_naive)))
                
    closest = query.first()
                
    if not closest:
        # If no older/newer scan found in that direction, fallback to closest overall
        closest = db.query(models.CameraScan.id, models.CameraScan.timestamp)\
                    .filter(models.CameraScan.camera_id == camera_id, models.CameraScan.has_image == True)\
                    .order_by(func.abs(func.julianday(models.CameraScan.timestamp) - func.julianday(dt_naive)))\
                    .first()
    
    if not closest:
        raise HTTPException(status_code=404, detail="No scans found for this camera")
        
    return {"id": closest.id, "timestamp": closest.timestamp.isoformat() + "Z"}

@app.get("/api/cameras/{camera_id}/inference-stats")
def get_camera_inference_stats(camera_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth), perms: List[str] = Depends(get_user_perms)):
    has_permission("view_metrics", perms)
    return crud.get_inference_stats(db, camera_id)

@app.get("/api/inference-stats")
def get_overall_inference_stats(db: Session = Depends(get_db), user: models.User = Depends(require_auth), perms: List[str] = Depends(get_user_perms)):
    has_permission("view_metrics", perms)
    return crud.get_overall_inference_stats(db)

# History Review
@app.get("/api/history/oldest-snapshot")
def get_oldest_snapshot(camera_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_history"):
        raise HTTPException(status_code=403, detail="Missing required permission: view_history")
    chunks = database.snapshot_db.chunks
    if not chunks:
        return {"oldest_timestamp": None}
    oldest_scan_id = chunks[0]["epoch_start"]
    scan = db.query(models.CameraScan.timestamp)\
             .filter(
                 models.CameraScan.camera_id == camera_id,
                 models.CameraScan.id >= oldest_scan_id
             )\
             .order_by(models.CameraScan.timestamp.asc()).first()
    if not scan:
        return {"oldest_timestamp": None}
    ts_str = scan[0].isoformat()
    if not ts_str.endswith('Z') and '+' not in ts_str and not ts_str.endswith('00:00'):
        ts_str += 'Z'
    return {"oldest_timestamp": ts_str}

@app.get("/api/history/scans")
def list_camera_scans(
    camera_id: int,
    date: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_auth)
):
    if not auth.check_permission_direct(user, "view_history"):
        raise HTTPException(status_code=403, detail="Missing required permission: view_history")
    if start and end:
        return crud.get_scans_range(db, camera_id, start, end)
    return crud.get_scans_by_date(db, camera_id, date)

@app.get("/api/history/scans/{scan_id}")
def get_scan_details(scan_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_history"):
        raise HTTPException(status_code=403, detail="Missing required permission: view_history")
    details = crud.get_scan_details(db, scan_id)
    if not details:
        raise HTTPException(status_code=404, detail="Scan not found")
    return details

@app.get("/api/history/spaces/{space_id}/events")
async def list_space_events_range(space_id: int, start: str, end: str, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_history"):
        raise HTTPException(status_code=403, detail="Missing required permission: view_history")
    return crud.get_space_events_range(db, space_id, start, end)

# Spaces
@app.get("/api/spaces")
async def list_spaces(
    camera_id: Optional[int] = None,
    db: Session = Depends(get_db),
    token: str = Depends(optional_auth),
):
    """List spaces.  Pass ``?camera_id=N`` to filter; omit to get all."""
    return crud.get_spaces(db, camera_id)

@app.post("/api/spaces")
async def create_space(space: schemas.SpaceCreate, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_cameras")
    return crud.create_space(space, db)

@app.put("/api/spaces")
async def update_spaces(spaces: List[schemas.SpaceUpdate], db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_cameras")
    return crud.update_spaces(spaces, db)

@app.get("/api/history/spaces/{space_id}")
async def list_space_events(space_id: int, db: Session = Depends(get_db), token: str = Depends(optional_auth)):
    # Anyone who can view dashboard can likely see events
    events = crud.get_events(db, space_id)
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "event_type": e.event_type,
            # Check LMDB for crop existence
            "has_crop": database.crop_db.get(str(e.id), timestamp=e.id) is not None
        }
        for e in events
    ]

@app.get("/api/events/{event_id}/crop")
async def get_event_crop(event_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_history"):
        raise HTTPException(status_code=403, detail="Missing required permission: view_history")
    # Fetch exclusively from LMDB
    crop = database.crop_db.get(str(event_id), timestamp=event_id)
    
    if not crop:
        raise HTTPException(status_code=404, detail="Crop not found in storage")
    return Response(content=crop, media_type="image/png")

# Stats
@app.get("/api/stats")
async def get_stats(start: str, end: str, camera_ids: List[int] = Query(None), group_id: Optional[int] = Query(None), db: Session = Depends(get_db), user: models.User = Depends(require_auth), perms: List[str] = Depends(get_user_perms)):
    has_permission("view_metrics", perms)
    if group_id:
        from backend.models import CameraGroupMembership
        memberships = db.query(CameraGroupMembership).filter_by(camera_group_id=group_id).all()
        camera_ids = [m.camera_id for m in memberships]
        if not camera_ids:
            return []
    return crud.get_stats_aggregated(db, camera_ids, start, end)

@app.get("/api/stats/hourly")
async def get_stats_hourly(start: str, end: str, camera_ids: List[int] = Query(None), group_id: Optional[int] = Query(None), db: Session = Depends(get_db), user: models.User = Depends(require_auth), perms: List[str] = Depends(get_user_perms)):
    has_permission("view_metrics", perms)
    if group_id:
        from backend.models import CameraGroupMembership
        memberships = db.query(CameraGroupMembership).filter_by(camera_group_id=group_id).all()
        camera_ids = [m.camera_id for m in memberships]
        if not camera_ids:
            return []
    return crud.get_stats_hourly(db, camera_ids, start, end)

# Image blob
@app.get("/api/image")
async def get_image(scan_id: int, user: models.User = Depends(require_auth)):
    # Anyone who can view history can see this
    if not auth.check_permission_direct(user, "view_history"):
        raise HTTPException(status_code=403, detail="Missing required permission: view_history")
    
    # Fetch directly from LMDB
    img_data = database.snapshot_db.get(str(scan_id), timestamp=scan_id)
    if not img_data:
        raise HTTPException(status_code=404, detail="Image not found in storage")
    return Response(content=img_data, media_type="image/jpeg")

@app.get("/api/settings/storage-stats")
async def get_storage_stats(user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "edit_settings"):
        raise HTTPException(status_code=403, detail="Missing required permission: edit_settings")

    def _get_counts():
        db = database.SessionLocal()
        try:
            raw = db.query(models.Occupancy).count()
            hourly = db.query(models.OccupancyHourly).count()
            return raw, hourly
        finally:
            db.close()

    raw_rows, hourly_rows = await asyncio.to_thread(_get_counts)

    def _summarize(store):
        return {
            "total_size_bytes": store.total_size_bytes,
            "num_chunks": store.num_chunks,
            "total_entries": store.total_entries,
            "max_size_gb": round(store.max_size_bytes / (1024**3), 2),
        }

    total_db_size = os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0

    return {
        "snapshots": _summarize(database.snapshot_db),
        "crops": _summarize(database.crop_db),
        "sqlite": {
            "size_bytes": total_db_size,
            "raw_occupancy_est": raw_rows * 40,
            "hourly_data_est": hourly_rows * 60,
        },
    }

@app.get("/api/version")
def get_version():
    commit = config.APP_VERSION.split("-")[-1] if "-" in config.APP_VERSION else ""
    return {
        "version": config.APP_VERSION,
        "commit": commit,
        "github_url": "",
        "repo_url": "",
    }

@app.get("/api/settings/gpu-provider")
async def get_gpu_provider():
    # The probe calls into the C++ engine which creates an
    # Ort::SessionOptions and tries to dlopen the platform's GPU
    # provider library.  On a host with a broken/missing driver this
    # can take several seconds; running it on the event loop would
    # freeze every other HTTP request.  We offload to a worker
    # thread.
    probe = await asyncio.to_thread(inference._probe_gpu_provider_safely)
    return {
        "available": bool(probe.get("provider")),
        "provider": probe.get("provider", ""),
        "error": probe.get("error", ""),
    }

@app.get("/api/settings/{key}")
async def get_setting(key: str, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    # Settings can hold secrets (SMTP password) / host
    # paths, so an unauthenticated caller must NOT be able to read them.
    setting = crud.get_setting(db, key)
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")
    return setting

@app.put("/api/settings/{key}")
async def update_setting(key: str, setting_update: schemas.SettingUpdate, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "edit_settings"):
        raise HTTPException(status_code=403, detail="Missing required permission: edit_settings")

    if key == "ssl_enabled" and setting_update.value.lower() == "true":
        cert_setting = crud.get_setting(db, "ssl_cert_path")
        key_setting = crud.get_setting(db, "ssl_key_path")
        if not cert_setting or not key_setting or not cert_setting.value or not key_setting.value:
            raise HTTPException(status_code=400, detail="Cannot enable HTTPS: No SSL certificate has been uploaded or generated yet.")
        if not os.path.exists(cert_setting.value) or not os.path.exists(key_setting.value):
            raise HTTPException(status_code=400, detail="Cannot enable HTTPS: Configured SSL certificate or key file is missing on host.")

    if key == "inference_backend" and setting_update.value not in ("vulturevision", "marek_rcnn"):
        raise HTTPException(status_code=422, detail="Choose a supported occupancy model.")
    # SQLite may wait for a concurrent writer. Keep that wait off the event loop.
    result = await asyncio.to_thread(crud.update_setting, db, key, setting_update.value)
    # Settings that affect the VultureVision constructor (use_gpu,
    # max_resolution) need an engine rebuild on the next call. Without
    # this, the change is only picked up after the 60s poll in
    # inference.get_vulturevision(), which means the operator sees the
    # old engine still running for up to a minute after saving.
    if key in ("inference_device", "max_inference_resolution", "inference_backend"):
        inference.invalidate_config_cache()
    if key == "inference_interval":
        scheduler.notify_settings_changed()
    return result

def generate_self_signed_cert(data_dir: str, force: bool = False):
    import os
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    ssl_dir = os.path.join(data_dir, "ssl")
    os.makedirs(ssl_dir, exist_ok=True)
    
    key_path = os.path.join(ssl_dir, "self_signed.key")
    cert_path = os.path.join(ssl_dir, "self_signed.crt")
    
    if not force and os.path.exists(key_path) and os.path.exists(cert_path):
        return cert_path, key_path

    # Generate Private Key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    # Generate Self-Signed Cert
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "park-vis.local"),
    ])
    
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365 * 50))
        .sign(private_key, hashes.SHA256())
    )
    
    # Write to files
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
        
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        
    os.chmod(key_path, 0o600)
    return cert_path, key_path

@app.post("/api/settings/ssl/upload")
async def upload_ssl_certs(
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(require_auth)
):
    if not auth.check_permission_direct(user, "edit_settings"):
        raise HTTPException(status_code=403, detail="Missing required permission: edit_settings")
    
    import shutil
    ssl_dir = os.path.join(config.DATA_DIR, "ssl")
    os.makedirs(ssl_dir, exist_ok=True)
    
    cert_path = os.path.join(ssl_dir, "custom.crt")
    key_path = os.path.join(ssl_dir, "custom.key")
    
    try:
        with open(cert_path, "wb") as buffer:
            shutil.copyfileobj(cert.file, buffer)
        with open(key_path, "wb") as buffer:
            shutil.copyfileobj(key.file, buffer)
        os.chmod(key_path, 0o600)
    except Exception as e:
        logger.error("Failed to save custom SSL files: {e}", e=e)
        raise HTTPException(status_code=500, detail=f"Failed to save certificate files: {e}")
        
    crud.update_setting(db, "ssl_cert_path", cert_path)
    crud.update_setting(db, "ssl_key_path", key_path)
    return {"status": "success"}

@app.post("/api/settings/ssl/generate-self-signed")
async def generate_self_signed_endpoint(
    force: bool = False,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_auth)
):
    if not auth.check_permission_direct(user, "edit_settings"):
        raise HTTPException(status_code=403, detail="Missing required permission: edit_settings")
        
    try:
        cert_path, key_path = generate_self_signed_cert(config.DATA_DIR, force=force)
    except Exception as e:
        logger.error("Failed to generate self-signed SSL certificate: {e}", e=e)
        raise HTTPException(status_code=500, detail=f"Failed to generate certificate: {e}")
        
    crud.update_setting(db, "ssl_cert_path", cert_path)
    crud.update_setting(db, "ssl_key_path", key_path)
    return {"status": "success"}


@app.get("/api/dashboard/full")
async def get_dashboard_full(db: Session = Depends(get_db), user: models.User = Depends(optional_auth)):
    """Returns EVERYTHING needed for the dashboard in one optimized call."""
    cameras = db.query(models.Camera).filter_by(is_enabled=True).all()
    all_spaces = db.query(models.Space).all()
    
    # Group spaces by camera
    spaces_map = {}
    for s in all_spaces:
        spaces_map.setdefault(s.camera_id, []).append({
            "id": s.id,
            "camera_id": s.camera_id,
            "name": s.name,
            "points": s.points # JSON string
        })
        
    results = []
    for cam in cameras:
        # Get live state from cache
        live_state = scheduler.get_latest_state(cam.id)
        
        results.append({
            "id": cam.id,
            "name": cam.name,
            "source_type": cam.source_type,
            "spaces": spaces_map.get(cam.id, []),
            "metadata": live_state.get("metadata", {}),
            "occupancy": {sid: data for sid, data in live_state.items() if sid != "metadata"}
        })
    return results

# Live Occupancy Endpoints
@app.get("/api/live/all")
async def get_all_live_occupancy(user: models.User = Depends(optional_auth)):
    """Returns the current state of all cameras in one call."""
    return scheduler._state_cache

@app.get("/api/live/{camera_id}")
async def get_camera_live_occupancy(camera_id: int, db: Session = Depends(get_db), user: models.User = Depends(optional_auth)):
    """Returns the most recent occupancy state for all spaces on a specific camera."""
    cam = db.query(models.Camera).filter_by(id=camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    return scheduler._state_cache.get(camera_id, {})

@app.websocket("/api/live/ws")
async def live_all_ws(websocket: WebSocket):
    """A single global websocket that broadcasts updates for ALL cameras.

    Each connected client polls once per second.  Two queries per
    tick (the ``inference_interval`` setting and the enabled-cameras
    set) used to be issued directly on the ASGI event loop thread,
    once per second per client.  Under SQLite lock contention (VACUUM,
    large transactions, snapshot writes) those queries can block the
    whole server.  The fix is a small per-process cache with a short
    TTL so we only re-query when the value might have changed.
    """
    await websocket.accept()
    logger.info("[WS] Global connection established")

    # We track the last scan ID and processing state for EVERY camera to know when to broadcast
    last_sent_scan_ids = {} # {camera_id: scan_id}
    last_sent_processing_states = {} # {camera_id: bool}

    try:
        while True:
            # Grab current state (shallow copy to avoid mutation during iteration)
            full_state = scheduler._state_cache.copy()
            updates = {}

            # Read the two slowly-changing values from cache.  The
            # cache is shared across all WS clients; one query per
            # cache lifetime serves every connected dashboard.
            interval = _get_inference_interval_cached()
            enabled_cams = _get_enabled_cams_cached()

            updates["_system"] = {"inference_interval": interval}

            for cam_id, cam_data in full_state.items():
                if cam_id not in enabled_cams: continue

                metadata = cam_data.get("metadata", {})
                current_scan_id = metadata.get("scan_id")
                current_processing = metadata.get("is_processing", False)

                # Check if this camera has a new scan OR a processing state change
                has_new_scan = current_scan_id and current_scan_id != last_sent_scan_ids.get(cam_id)
                has_processing_change = current_processing != last_sent_processing_states.get(cam_id)

                if has_new_scan or has_processing_change:
                    updates[cam_id] = cam_data
                    last_sent_scan_ids[cam_id] = current_scan_id
                    last_sent_processing_states[cam_id] = current_processing

            if updates:
                await websocket.send_json(updates)

            await asyncio.sleep(1)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.debug("WS error: {e}", e=e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

@app.websocket("/api/live/{camera_id}")
async def legacy_ws_closer(websocket: WebSocket, camera_id: int):
    """Gracefully handles and closes connections to legacy per-camera endpoints without logging."""
    try:
        # We accept and then immediately close with a terminal code.
        # This is more effective than a 410 rejection for stopping browser loops.
        await websocket.accept()
        await websocket.close(code=1001)
    except Exception:
        pass

# H13 audit fix: the previous code had a duplicate ``legacy_ws_closer``
# function here declared as a GET handler with a ``WebSocket`` parameter
# that FastAPI cannot inject into an HTTP route — the route would fail
# at request time, and the duplicate name also shadowed the websocket
# function.  A GET to ``/api/live/{camera_id}`` is not a documented
# endpoint and the frontend never calls it (the canonical live endpoint
# is the single global socket at ``/api/live/ws``).  Removed entirely;
# if a client somehow sends a GET, FastAPI will return a clean 405 /
# 404 instead of a confusing internal error.

@app.post("/api/cameras/test")
async def create_test_camera(
    name: str = Query(...), 
    camera_id: Optional[int] = Query(None),
    file: UploadFile = File(...), 
    db: Session = Depends(get_db), 
    user: models.User = Depends(require_auth)
):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_cameras")
    # Save file
    file_ext = os.path.splitext(file.filename)[1]
    file_name = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join("uploads/cameras", file_name)
    
    os.makedirs("uploads/cameras", exist_ok=True)
    with open(file_path, "wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            buffer.write(chunk)
    
    if camera_id:
        db_cam = db.query(models.Camera).filter_by(id=camera_id).first()
        if not db_cam:
            raise HTTPException(status_code=404, detail="Camera not found")
        db_cam.name = name
        db_cam.source_type = "test"
        db_cam.is_test = True
        db_cam.local_path = file_path
        db_cam.snapshot_url = None
        db.commit()
        db.refresh(db_cam)
        scheduler.trigger_inference()
        return db_cam
    else:
        db_cam = models.Camera(
            name=name,
            source_type="test",
            is_test=True,
            local_path=file_path
        )
        db.add(db_cam)
        db.commit()
        db.refresh(db_cam)
        scheduler.trigger_inference()
        return db_cam

@app.post("/api/cameras/video")
async def upload_camera_video(
    name: str = Query(...), 
    camera_id: Optional[int] = Query(None),
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_auth)
):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing permission: manage_cameras")
    
    # Save the file
    file_id = str(uuid.uuid4())
    ext = file.filename.split('.')[-1]
    filename = f"video_{file_id}.{ext}"
    file_path = os.path.join("uploads/cameras", filename)
    
    os.makedirs("uploads/cameras", exist_ok=True)
    with open(file_path, "wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024) # 1MB chunks
            if not chunk:
                break
            buffer.write(chunk)
    
    if camera_id:
        db_cam = db.query(models.Camera).filter_by(id=camera_id).first()
        if not db_cam:
            raise HTTPException(status_code=404, detail="Camera not found")
        db_cam.name = name
        db_cam.source_type = "video"
        db_cam.stream_url = file_path # We store file path in stream_url for video types
        db.commit()
        db.refresh(db_cam)
        scheduler.trigger_inference()
        return db_cam
    else:
        db_cam = models.Camera(
            name=name,
            source_type="video",
            stream_url=file_path,
            is_enabled=True
        )
        db.add(db_cam)
        db.commit()
        db.refresh(db_cam)
        scheduler.trigger_inference()
        return db_cam

@app.get("/api/cameras/{camera_id}/stream")
async def stream_camera(camera_id: int, request: Request, db: Session = Depends(database.get_db)):
    cam = db.query(models.Camera).filter_by(id=camera_id).first()
    if not cam or cam.source_type not in ["rtsp", "youtube", "video"]:
        raise HTTPException(status_code=404, detail="Stream not available for this camera")
    
    # Ensure stream is started
    stream_manager.start_stream(cam.id, cam.source_type, cam.stream_url, cam.stream_user, cam.stream_password, res=cam.stream_resolution, fps=cam.stream_fps)

    async def generate():
        try:
            while stream_manager.manager_active:
                if await request.is_disconnected():
                    logger.info("[STREAM] Client disconnected from camera {cid}", cid=camera_id)
                    break

                frame = stream_manager.get_latest_frame(cam.id)
                if frame is not None:
                    # Convert to JPEG
                    _, buffer = cv2.imencode('.jpg', frame)
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                await asyncio.sleep(0.1) # ~10 FPS for the UI
        except asyncio.CancelledError:
            logger.info("[STREAM] Stream generation cancelled for camera {cid}", cid=camera_id)
        finally:
            logger.info("[STREAM] Generator exiting for camera {cid}", cid=camera_id)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")

# --- High Performance Thumbnail Caching ---
if not hasattr(app, "thumbnail_cache"):
    app.thumbnail_cache = {} # {(camera_id, scan_id, target_width): resized_bytes}

# --- Live Frame Cache ---
# Holds the most recently served LIVE image bytes per camera_id, so
# the editor's "Test" click runs inference on the EXACT bytes the
# editor is displaying. Without this, every snapshot/test call to
# stream_manager.get_latest_frame() returns a *different* frame on
# a fast-changing stream — the displayed image stays the same
# (browser-cached) but the inference result flips between clicks.
# The cache key is camera_id; the value is (timestamp, jpeg_bytes).
# Stale entries (older than ``_LIVE_FRAME_TTL_SECONDS``) are treated
# as cache misses so a stream that genuinely stops producing frames
# doesn't pin a stale image forever.
if not hasattr(app, "live_frame_cache"):
    app.live_frame_cache = {}  # {camera_id: (timestamp, jpeg_bytes)}

_LIVE_FRAME_TTL_SECONDS = 30.0

# --- Live Dashboard Broadcast Cache ---
# The /api/live/ws WebSocket endpoint used to issue 2 sync SQLite
# queries on the ASGI event loop thread, once per second per
# connected client.  Under SQLite lock contention (VACUUM, large
# transactions) those queries can block the whole server.  We cache
# both values with a short TTL — one query per cache lifetime
# serves every connected dashboard.  ``inference_interval`` is
# changed by an admin in Settings; ``enabled_cams`` is changed by
# the camera-toggle switch.  Both are rare, so a 5-second TTL is
# imperceptible to the user.
_LIVE_BROADCAST_CACHE_TTL_SECONDS = 5.0
if not hasattr(app, "live_broadcast_cache"):
    app.live_broadcast_cache = {
        "interval": (0.0, 60),  # (timestamp, value)
        "enabled_cams": (0.0, set()),
    }


def _get_inference_interval_cached() -> int:
    """Return the current ``inference_interval`` setting, cached for
    ``_LIVE_BROADCAST_CACHE_TTL_SECONDS``.

    The cache key is just the value; we ignore changes mid-cache.
    Worst case: a user changes the interval and dashboards see the
    old value for up to 5s.  That's fine.
    """
    now = time.monotonic()
    ts, value = app.live_broadcast_cache["interval"]
    if (now - ts) < _LIVE_BROADCAST_CACHE_TTL_SECONDS:
        return value
    db = database.SessionLocal()
    try:
        s = db.query(models.Setting).filter_by(key="inference_interval").first()
        value = int(s.value) if s else 60
    finally:
        db.close()
    app.live_broadcast_cache["interval"] = (now, value)
    return value


def _get_enabled_cams_cached() -> set:
    """Return the set of enabled camera ids, cached for
    ``_LIVE_BROADCAST_CACHE_TTL_SECONDS``.
    """
    now = time.monotonic()
    ts, value = app.live_broadcast_cache["enabled_cams"]
    if (now - ts) < _LIVE_BROADCAST_CACHE_TTL_SECONDS:
        return value
    db = database.SessionLocal()
    try:
        value = {
            c.id
            for c in db.query(models.Camera.id).filter_by(is_enabled=True).all()
        }
    finally:
        db.close()
    app.live_broadcast_cache["enabled_cams"] = (now, value)
    return value


def clear_thumbnail_cache():
    app.thumbnail_cache.clear()


def get_cached_live_frame(camera_id: int) -> Optional[bytes]:
    """Return the most recently served LIVE bytes for ``camera_id``,
    or None if the cache is empty or stale."""
    entry = app.live_frame_cache.get(camera_id)
    if not entry:
        return None
    ts, data = entry
    if (time.time() - ts) > _LIVE_FRAME_TTL_SECONDS:
        # Treat as a miss; don't keep returning an old frame.
        return None
    return data


def set_cached_live_frame(camera_id: int, data: bytes) -> None:
    app.live_frame_cache[camera_id] = (time.time(), data)


def clear_live_frame_cache_for(camera_id: int) -> None:
    """Drop the cached LIVE frame for ``camera_id``.

    Called by ``GET /api/cameras/{id}/snapshot?force=true`` so an
    explicit Refresh actually re-grabs from the stream instead of
    returning a cached frame the user is already looking at.
    """
    app.live_frame_cache.pop(camera_id, None)


def get_thumbnail_cache_state():
    return app.thumbnail_cache

def get_cached_thumbnail(camera_id: int, scan_id: Optional[int], target_width: int, image_bytes: bytes):
    """
    Returns a resized thumbnail, using an in-memory cache to avoid repeated work.
    """
    key = (camera_id, scan_id or time.time() // 10, target_width)
    
    if key in app.thumbnail_cache:
        return app.thumbnail_cache[key]
    
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: 
            app.thumbnail_cache[key] = image_bytes
            return image_bytes
        
        h_orig, w_orig = img.shape[:2]
        if w_orig <= target_width:
            app.thumbnail_cache[key] = image_bytes
            return image_bytes
            
        h_new = int((h_orig / w_orig) * target_width)
        resized = cv2.resize(img, (target_width, h_new), interpolation=cv2.INTER_AREA)
        _, buffer = cv2.imencode('.jpg', resized, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        
        if len(app.thumbnail_cache) > 200:
            app.thumbnail_cache.clear()
            
        result = buffer.tobytes()
        app.thumbnail_cache[key] = result
        return result
    except Exception as e:
        logger.warning("[THUMBNAIL] Error: {e}", e=e)
        app.thumbnail_cache[key] = image_bytes
        return image_bytes

async def finalize_image_response(img_data: bytes, width: Optional[int] = None, camera_id: Optional[int] = None, v: Optional[int] = None):
    # Resize if requested
    if width:
        # Use the high-performance resizer with metadata-only cache keys
        # v is the scan_id
        resized_data = await asyncio.to_thread(get_cached_thumbnail, camera_id, v, width, img_data)
        return Response(content=resized_data, media_type="image/jpeg")

    return Response(content=img_data, media_type="image/jpeg")

async def _acquire_camera_image_bytes(cam: models.Camera, v: Optional[str], db: Session) -> bytes:
    """Fetch JPEG bytes for ``cam``, honoring an archived ``v`` (scan_id) if present.

    Used by both ``GET /api/cameras/{id}/snapshot`` and
    ``POST /api/cameras/{id}/test-occupancy`` so the editor's *displayed*
    image and the AI's *inference* image are guaranteed to be byte-for-byte
    identical. Without this unification, a LIVE test would re-grab a
    fresh stream frame and re-encoded at default JPEG quality ~95 while
    the displayed image was the previous frame at ``quality_snapshots=85``,
    causing the spot occupancy to disagree with what the user sees — a
    fast-changing stream would make this obvious within seconds.

    When ``v`` is set we return the LMDB blob (the JPEG the scheduler
    actually wrote). When it's null we return what the snapshot endpoint
    would return: file-read for a test/static camera, a fresh frame
    re-encoded as JPEG for live streams, or the upstream ``snapshot_url``
    response for a standard URL-based camera.

    For LIVE streams (rtsp/youtube/video), the *first* request after a
    fresh image lands fetches a frame, JPEG-encodes it, and stores it in
    ``app.live_frame_cache`` keyed by camera_id. Subsequent requests
    within ``_LIVE_FRAME_TTL_SECONDS`` return the same bytes — so a
    Test click always runs inference on the *displayed* frame, not on
    whatever the stream produced in the milliseconds between display
    and click. Cache misses (TTL expired, or first request) fall
    through to a fresh grab.
    """
    if v:
        img_data = database.snapshot_db.get(str(v), timestamp=int(v))
        if not img_data:
            raise HTTPException(status_code=404, detail="Archived image not found (it may have been pruned)")
        return img_data

    if cam.source_type == "test" or (cam.is_test and cam.local_path):
        if not cam.local_path or not os.path.exists(cam.local_path):
            raise HTTPException(status_code=404, detail="Local snapshot file not found")
        # ``with`` so the file handle is released even if the worker
        # thread is interrupted. The original lambda leaked the handle
        # on every test-camera snapshot fetch.
        def _read_local():
            with open(cam.local_path, "rb") as f:
                return f.read()
        return await asyncio.to_thread(_read_local)

    if cam.source_type in ["rtsp", "youtube", "video"]:
        # Serve a cached frame if one is fresh. This is the single
        # mechanism that makes the editor's "Test" button deterministic
        # on a LIVE stream — without it, every Test click re-grabs
        # stream_manager.get_latest_frame(), which advances to the
        # newest available frame and produces a different inference
        # result on every click.
        cached = get_cached_live_frame(cam.id)
        if cached is not None:
            return cached

        for _ in range(50):  # 50 * 0.1s = 5s
            frame = stream_manager.get_latest_frame(cam.id)
            if frame is not None:
                _, buffer = await asyncio.to_thread(cv2.imencode, '.jpg', frame)
                data = buffer.tobytes()
                set_cached_live_frame(cam.id, data)
                return data

            stream_manager.start_stream(
                cam.id,
                cam.source_type,
                cam.stream_url,
                cam.stream_user,
                cam.stream_password,
                res=cam.stream_resolution,
                fps=cam.stream_fps,
                youtube_mode=getattr(cam, 'youtube_mode', 'stream')
            )
            await asyncio.sleep(0.1)

        raise HTTPException(status_code=503, detail="Stream starting, please retry in a moment")

    if not cam.snapshot_url:
        raise HTTPException(status_code=400, detail="Camera has no snapshot URL configured")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(cam.snapshot_url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch snapshot: {str(e)}")


@app.get("/api/cameras/{camera_id}/snapshot")
async def get_camera_snapshot(camera_id: int, v: Optional[str] = Query(None), width: Optional[int] = Query(None), force: bool = Query(False), db: Session = Depends(get_db)):
    cam = None
    if not v:
        cam = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
        if not cam:
            raise HTTPException(status_code=404, detail="Camera not found")
    # ``force=true`` invalidates the LIVE frame cache so the next
    # call (this one) re-grabs a fresh frame from the stream. The
    # editor passes ``force=true`` on the Refresh button so the
    # displayed image actually updates; subsequent Test clicks
    # (without ``force``) will then deterministically run inference
    # on the new frame for the next ``_LIVE_FRAME_TTL_SECONDS``.
    if force and not v:
        clear_live_frame_cache_for(cam.id)
    img_data = await _acquire_camera_image_bytes(cam, v, db)
    return await finalize_image_response(img_data, width, camera_id, v)

@app.post("/api/cameras/{camera_id}/test-occupancy")
async def test_occupancy(camera_id: int, spaces: List[schemas.SpaceUpdate], v: Optional[int] = Query(None), db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_cameras")

    cam = db.query(models.Camera).filter_by(id=camera_id).first()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")

    try:
        # Acquire the EXACT same JPEG bytes the editor displayed. This
        # is critical: a LIVE test that re-grabs a fresh stream frame
        # (or re-encodes at default ~95 quality) will produce different
        # spot occupancy than the user sees on the page. The snapshot
        # endpoint already does the right thing — share the path.
        content = await _acquire_camera_image_bytes(cam, str(v) if v else None, db)

        # Fetch hysteresis settings
        occ_thresh_setting = db.query(models.Setting).filter_by(key="hysteresis_occupied_threshold").first()
        occ_thresh = float(occ_thresh_setting.value) if occ_thresh_setting else 0.75

        free_thresh_setting = db.query(models.Setting).filter_by(key="hysteresis_free_threshold").first()
        free_thresh = float(free_thresh_setting.value) if free_thresh_setting else 0.25

        # Default to 1440 to match the validation pipeline. See
        # backend/inference.py for the full rationale.
        max_res_setting = db.query(models.Setting).filter_by(key="max_inference_resolution").first()
        max_res = int(max_res_setting.value) if max_res_setting else 1440

        # Prepare all spaces for inference using index as a temporary ID
        spaces_data = [{'id': i, 'points': s.points} for i, s in enumerate(spaces)]

        probs_dict, _, _ = await inference.run_inference(camera_id, content, spaces_data, max_res=max_res)

        # Map probabilities to booleans using hysteresis (defaulting to False for 'previous' state in manual test)
        results = []
        for i in range(len(spaces)):
            prob = probs_dict.get(i, 0.0)
            if prob > occ_thresh:
                results.append(True)
            elif prob < free_thresh:
                results.append(False)
            else:
                # In manual test, we don't have an easily accessible "previous" state,
                # so we fallback to a simple 0.5 threshold for the uncertain zone.
                results.append(prob >= 0.5)

        return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _compute_range(store, db, is_crop):
    """Return oldest/newest range from actual LMDB data + SQLite timestamps.

    Queries SQLite for timestamps but derives IDs from the chunks that
    actually exist in LMDB — avoids pointing at IDs whose blobs have
    already been evicted.
    """
    all_chunks = store.chunks
    if not all_chunks:
        return {"oldest": None, "newest": None}

    # Oldest entry = epoch_start of the oldest chunk
    oldest_id = all_chunks[0]["epoch_start"]
    # Newest entry ≈ epoch_start + num_entries - 1 of the newest chunk
    newest_chunk = all_chunks[-1]
    newest_id = newest_chunk["epoch_start"] + newest_chunk["num_entries"] - 1

    oldest_ts = newest_ts = None
    if oldest_id:
        row = (db.query(models.OccupancyEvent.timestamp).filter_by(id=oldest_id).first()
               if is_crop else
               db.query(models.CameraScan.timestamp).filter_by(id=oldest_id).first())
        if row: oldest_ts = row.timestamp.isoformat()
    if newest_id:
        row = (db.query(models.OccupancyEvent.timestamp).filter_by(id=newest_id).first()
               if is_crop else
               db.query(models.CameraScan.timestamp).filter_by(id=newest_id).first())
        if row: newest_ts = row.timestamp.isoformat()

    return {"oldest": {"id": oldest_id, "timestamp": oldest_ts},
            "newest": {"id": newest_id, "timestamp": newest_ts}}


def _enrich_chunk_timestamps(chunks: list, db: Session, is_crop: bool) -> list[dict]:
    """Augment each chunk dict with ``first_timestamp`` / ``last_timestamp``
    by looking up the SQLite rows for the first and last ID in the chunk."""
    model_cls = models.OccupancyEvent if is_crop else models.CameraScan
    ts_col = models.OccupancyEvent.timestamp if is_crop else models.CameraScan.timestamp

    enriched = []
    for ch in chunks:
        first_id = ch["epoch_start"]
        n = ch["num_entries"]
        last_id = first_id + n - 1 if n > 0 else None

        first_ts = db.query(ts_col).filter(model_cls.id == first_id).scalar()
        last_ts = db.query(ts_col).filter(model_cls.id == last_id).scalar() if last_id else None

        enriched.append({
            **ch,
            "first_timestamp": first_ts.isoformat() if first_ts else None,
            "last_timestamp": last_ts.isoformat() if last_ts else None,
        })
    return enriched


def _build_db_info(store, db, is_crop):
    """Rich info for one RollingLMDB instance."""
    return {
        "path": store.directory,
        "config": {
            "max_size_bytes": store.max_size_bytes,
            "max_size_gb": round(store.max_size_bytes / (1024**3), 2),
            "chunk_size_bytes": store.chunk_size_bytes,
            "chunk_size_mb": store.chunk_size_bytes // (1024 * 1024),
        },
        "summary": {
            "num_chunks": store.num_chunks,
            "total_entries": store.total_entries,
            "total_size_bytes": store.total_size_bytes,
        },
        "range": _compute_range(store, db, is_crop),
        "chunks": _enrich_chunk_timestamps(store.chunks, db, is_crop),
    }


@app.get("/api/admin/storage/stats")
def get_storage_diagnostics(db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")

    import shutil
    try:
        stat = shutil.disk_usage(config.DATA_DIR)
        disk_info = {
            "total_bytes": stat.total,
            "used_bytes": stat.used,
            "free_bytes": stat.free,
        }
    except Exception as e:
        logger.error("Failed to get disk usage info: {e}", e=e)
        disk_info = None

    return {
        "sqlite": {
            "size_bytes": os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0,
            "scans_count": db.query(models.CameraScan).count(),
            "events_count": db.query(models.OccupancyEvent).count(),
        },
        "snapshots": _build_db_info(database.snapshot_db, db, is_crop=False),
        "crops": _build_db_info(database.crop_db, db, is_crop=True),
        "disk": disk_info,
    }

@app.post("/api/admin/storage/prune")
def manual_prune(db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")
    
    logger.info("[ADMIN] Manual prune triggered...")
    try:
        from .maintenance import run_maintenance
        run_maintenance(db)
        return {"message": "Retention policy executed successfully. Old records and blobs pruned."}
    except Exception as e:
        logger.error("[ADMIN] Prune failed: {e}", e=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/storage/optimize")
def manual_optimize(db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_users"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    logger.info("[ADMIN] Manual optimization (VACUUM) triggered...")
    try:
        # VACUUM cannot run inside a transaction.
        # We must use the underlying connection and ensure it's in autocommit mode.
        db.execute(text("COMMIT")) # End any existing transaction
        db.execute(text("VACUUM"))
        return {"message": "SQLite VACUUM complete. Databases optimized."}
    except Exception as e:
        logger.error("[ADMIN] Optimization failed: {e}", e=e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# RollingLMDB browsing (admin, view_diagnostics required)
# ---------------------------------------------------------------------------

def _resolve_store(db_name: str):
    if db_name == "snapshots":
        return database.snapshot_db
    if db_name == "crops":
        return database.crop_db
    raise HTTPException(status_code=400, detail="db must be 'snapshots' or 'crops'")


@app.get("/api/admin/storage/chunks")
def list_storage_chunks(db: Session = Depends(get_db), user: models.User = Depends(require_auth),
                        db_name: str = Query("snapshots", alias="db")):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")
    store = _resolve_store(db_name)
    return {"chunks": _enrich_chunk_timestamps(store.chunks, db, db_name == "crops")}


@app.get("/api/admin/storage/chunks/{chunk_index}/keys")
def list_chunk_keys(chunk_index: int, db: Session = Depends(get_db),
                    user: models.User = Depends(require_auth),
                    db_name: str = Query("snapshots", alias="db"),
                    offset: int = Query(0, ge=0),
                    limit: int = Query(0, ge=0)):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")
    store = _resolve_store(db_name)
    if chunk_index < 0 or chunk_index >= store.num_chunks:
        raise HTTPException(status_code=404, detail=f"Chunk index {chunk_index} out of range (0-{store.num_chunks - 1})")

    if limit > 0:
        entries, total_count = store.list_keys_page(chunk_index, offset, limit)
        return {
            "chunk_index": chunk_index,
            "chunk": store.chunks[chunk_index],
            "keys": entries,
            "total_count": total_count,
            "offset": offset,
            "limit": limit,
        }
    else:
        keys = store.list_keys(chunk_index)
        return {
            "chunk_index": chunk_index,
            "chunk": store.chunks[chunk_index],
            "keys": keys,
            "total_count": len(keys),
        }


@app.get("/api/admin/storage/blob/{key}")
def get_storage_blob(key: str, db: Session = Depends(get_db),
                     user: models.User = Depends(require_auth),
                     db_name: str = Query("snapshots", alias="db"),
                     timestamp: Optional[int] = Query(None)):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")
    store = _resolve_store(db_name)
    ts = timestamp
    if ts is None:
        # Try to infer timestamp from the newest chunk containing this key
        for chunk in reversed(store._collection._chunks):
            if chunk.get(key) is not None:
                ts = chunk.epoch_start
                break
    if ts is None:
        raise HTTPException(status_code=404, detail="Key not found in any chunk")
    data = store.get(key, timestamp=ts)
    if data is None:
        raise HTTPException(status_code=404, detail="Key not found in storage")
    # Try to guess media type from magic bytes
    media_type = "application/octet-stream"
    if data[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    elif data[:4] == b"\x89PNG":
        media_type = "image/png"
    return Response(content=data, media_type=media_type)


@app.get("/api/admin/debug/heap")
async def get_heap_stats(
    include_heap: bool = Query(False),
    user: models.User = Depends(require_auth)
):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")

    import tracemalloc
    is_tracing = tracemalloc.is_tracing()
    
    # Process basic info (always return this)
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem = process.memory_info()
        proc_data = {
            "pid": os.getpid(),
            "rss_bytes": int(mem.rss),
            "vms_bytes": int(mem.vms),
            "cpu_percent": float(process.cpu_percent(interval=None) or 0.0)
        }
    except Exception as exc:
        # psutil can raise on platforms where the process disappeared,
        # or under weird permission setups. Fall back to zeros and log
        # the real reason instead of silently masking it.
        logger.debug("psutil memory_info failed: {e}", e=exc)
        proc_data = {"pid": os.getpid(), "rss_bytes": 0, "vms_bytes": 0, "cpu_percent": 0.0}

    # If not tracing or include_heap is False, return early with process info
    if not is_tracing or not include_heap:
        # We still want traces if tracing is enabled, even if heap scan is skipped
        traces = []
        if is_tracing:
            snapshot = await asyncio.to_thread(tracemalloc.take_snapshot)
            top_stats = snapshot.statistics('lineno')
            for stat in top_stats[:50]:
                traces.append({
                    "file": stat.traceback[0].filename,
                    "line": stat.traceback[0].lineno,
                    "size_bytes": stat.size,
                    "count": stat.count
                })

        return {
            "tracing_enabled": is_tracing,
            "process": proc_data,
            "heap": [],
            "traces": traces
        }

    try:
        from pympler import muppy, summary
        import gc
        
        # Capture state (EXPENSIVE - move to thread)
        def capture_heap():
            all_objs = muppy.get_objects()
            return summary.summarize(all_objs)
            
        summ = await asyncio.to_thread(capture_heap)
        
        # Sort and format top objects
        res = []
        for row in summ:
            try:
                # row is: [type_description, count, size]
                t_name = str(row[0]) if len(row) > 0 else "Unknown"
                count = int(row[1]) if len(row) > 1 else 0
                size = int(row[2]) if len(row) > 2 else 0
                
                res.append({
                    "type": t_name,
                    "count": count,
                    "size_bytes": size
                })
            except Exception:
                continue
            
        res.sort(key=lambda x: x.get('size_bytes', 0), reverse=True)
        
        # We also return traces here if tracing is on
        traces = []
        if tracemalloc.is_tracing():
            snapshot = await asyncio.to_thread(tracemalloc.take_snapshot)
            top_stats = snapshot.statistics('lineno')
            for stat in top_stats[:50]:
                traces.append({
                    "file": stat.traceback[0].filename,
                    "line": stat.traceback[0].lineno,
                    "size_bytes": stat.size,
                    "count": stat.count
                })

        return {
            "tracing_enabled": is_tracing,
            "process": proc_data,
            "heap": res[:50],
            "traces": traces
        }
    except ImportError:
        return {"error": "Pympler or psutil not installed in the environment."}
    except Exception as e:
        logger.error("Heap stats failed: {e}", e=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/debug/gc")
async def trigger_gc(user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")
    import gc
    collected = gc.collect()
    return {"message": f"Garbage collection triggered. Collected {collected} objects."}

@app.get("/api/admin/debug/streams")
async def get_stream_diagnostics(db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")
    
    raw_stats = stream_manager.get_diagnostics()
    cameras = {c.id: c.name for c in db.query(models.Camera).filter(models.Camera.id.in_(list(raw_stats.keys()))).all()}
    
    results = []
    for cid, stat in raw_stats.items():
        results.append({
            "camera_id": cid,
            "camera_name": cameras.get(cid, f"Camera {cid}"),
            **stat
        })
    return results

@app.get("/api/admin/debug/threads")
async def get_thread_diagnostics(user: models.User = Depends(require_auth)):
    """Capture a snapshot of all active Python threads and their current live stack traces."""
    if not auth.check_permission_direct(user, "view_diagnostics"):
        raise HTTPException(status_code=403, detail="Missing permission: view_diagnostics")

    def _capture_threads():
        threads_map = {t.ident: t for t in threading.enumerate()}
        current_frames = sys._current_frames()
        results = []
        current_ident = threading.get_ident()

        for ident, frame in current_frames.items():
            thread_obj = threads_map.get(ident)
            thread_name = thread_obj.name if thread_obj else f"Thread-{ident}"
            is_daemon = thread_obj.daemon if thread_obj else False
            is_alive = thread_obj.is_alive() if thread_obj else True

            raw_stack = traceback.extract_stack(frame)
            stack_frames = []
            for item in raw_stack:
                stack_frames.append({
                    "file": item.filename,
                    "line": item.lineno,
                    "func": item.name,
                    "code": item.line or ""
                })

            formatted_tb = "".join(traceback.format_stack(frame))

            results.append({
                "ident": ident,
                "name": thread_name,
                "is_daemon": is_daemon,
                "is_alive": is_alive,
                "is_current": (ident == current_ident),
                "frames_count": len(stack_frames),
                "top_frame": stack_frames[-1] if stack_frames else None,
                "stack": stack_frames,
                "formatted_traceback": formatted_tb
            })

        results.sort(key=lambda t: (not t["is_current"], t["name"] != "MainThread", t["name"]))
        return results

    threads_data = await asyncio.to_thread(_capture_threads)
    return {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "total_threads": len(threads_data),
        "threads": threads_data
    }

_LOG_HISTORY_MAX_LINES = 100
_LOG_HISTORY_CHUNK_BYTES = 64 * 1024


def _tail_lines(path, matches_filter, max_lines):
    """Read the last ``max_lines`` matching lines from the file at
    ``path`` in chronological order.

    Reads backwards in 64KB chunks (re-seek before each chunk),
    splits on ``\\n``, and keeps a fixed-size deque of matching lines.
    Memory usage is bounded by the chunk size and the deque
    capacity — independent of the file's total length.  Returns an
    empty list if the file doesn't exist, is empty, or no lines
    match.
    """
    from collections import deque

    if not os.path.exists(path):
        return []

    matches = deque(maxlen=max_lines)
    with open(path, "rb") as f:
        f.seek(0, 2)  # seek to end
        file_size = f.tell()
        if file_size == 0:
            return []

        pos = file_size
        # ``leftover`` holds the partial-line tail of the PREVIOUS
        # chunk (a chunk that didn't end on a ``\\n`` boundary).  We
        # prepend it to the next chunk we read to recover the
        # complete line.  Empty when no partial line is pending.
        leftover = b""

        while pos > 0 and len(matches) < max_lines:
            read_size = min(_LOG_HISTORY_CHUNK_BYTES, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            
            parts = chunk.split(b"\n")
            if leftover:
                # Since we are reading backward (from end of file to start), the leftover
                # comes from the start of the newer chunk we read in the previous iteration.
                # It belongs to the end of the last line in the current chunk we just read.
                parts[-1] = parts[-1] + leftover
                leftover = b""
                
            if pos > 0:
                # If we're not at the very start of the file, the first part in this chunk
                # is potentially a partial line that continues into the previous/earlier chunk.
                # Save it as leftover, and process the rest of the lines as complete.
                leftover = parts[0]
                complete_lines = parts[1:]
            else:
                # We've reached the start of the file, so all parts are complete.
                complete_lines = parts

            # Iterate newest-first within this chunk.
            for raw in reversed(complete_lines):
                try:
                    text = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if not text:
                    continue
                if matches_filter(text):
                    # ``appendleft`` keeps the deque in chronological
                    # order (oldest first) as we walk the file
                    # newest-to-oldest.
                    matches.appendleft(text)
                    if len(matches) >= max_lines:
                        break

    return list(matches)


@app.websocket("/api/admin/debug/logs/ws")
async def log_streaming_ws(websocket: WebSocket):
    # Auth is tricky over WS, but we can check token from query
    token = websocket.query_params.get("token")
    db = database.SessionLocal()
    try:
        user = auth.get_auth_user(websocket, db, token)
        if not user or not auth.check_permission_direct(user, "view_diagnostics"):
            await websocket.close(code=1008) # Policy Violation
            return
    finally:
        db.close()

    await websocket.accept()
    
    # Optional filters
    level_filter = websocket.query_params.get("level", "DEBUG").upper()
    grep_filter = websocket.query_params.get("grep", "").lower()

    def _matches_filter(msg):
        # 1. Level Filter
        if level_filter != "DEBUG":
            parts = msg.split("|", 2)
            if len(parts) >= 2:
                msg_level = parts[1].strip()
                levels = ["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]
                try:
                    if levels.index(msg_level) < levels.index(level_filter):
                        return False
                except ValueError: pass
        
        # 2. Grep Filter
        if grep_filter and grep_filter not in msg.lower():
            return False
            
        return True

    from .logging_config import log_streamer
    log_q = asyncio.Queue(maxsize=100)
    log_streamer.add_queue(log_q)

    try:
        # 1. Send some history first (Filtered).
        #
        # We tail-read the last ``_LOG_HISTORY_MAX_LINES`` matching
        # lines instead of using ``f.readlines()``.  A long-running
        # install can grow ``logs/vulture.log`` to hundreds of MB;
        # reading the whole file into a list would OOM the process.
        # The tail-read scans backwards in 64KB chunks and keeps
        # the most recent matches in a fixed-size deque.  Memory
        # usage stays bounded regardless of file size.
        history = _tail_lines("logs/vulture.log", _matches_filter, _LOG_HISTORY_MAX_LINES)
        for line in history:
            await websocket.send_text(line)

        # 2. Stream live (Filtered)
        while True:
            msg = await log_q.get()
            if _matches_filter(msg):
                await websocket.send_text(msg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        log_streamer.remove_queue(log_q)

@app.post("/api/admin/feedback/submit")
def submit_feedback(data: schemas.FeedbackSubmit, db: Session = Depends(get_db), user: models.User = Depends(require_auth)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(status_code=403, detail="Admin access required")

    # 1. Get original image from storage
    img_data = database.snapshot_db.get(str(data.scan_id), timestamp=data.scan_id)
    if not img_data:
        raise HTTPException(status_code=404, detail="Original image not found (it may have been pruned)")

    # 2. Get scan info for timestamp
    scan = db.query(models.CameraScan).filter_by(id=data.scan_id).first()
    ts_str = scan.timestamp.strftime("%Y%m%d_%H%M%S") if scan else "unknown"

    # 3. Save files
    base_name = f"cam{data.camera_id}_scan{data.scan_id}_{ts_str}"
    img_filename = f"{base_name}.jpg"
    json_filename = f"{base_name}.json"

    # Keep corrections and their source images in local application storage.
    images_dir = os.path.join(config.DATA_DIR, "training_feedback", "images")
    annotations_dir = os.path.join(config.DATA_DIR, "training_feedback", "annotations")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(annotations_dir, exist_ok=True)

    with open(os.path.join(images_dir, img_filename), "wb") as f:
        f.write(img_data)

    # Format: same as ACPDS (list of points)
    rois = []
    occupancy = []
    original_occupancy = []
    corrections = []
    for s in data.spaces:
        # points are [x1, y1, x2, y2, ...] - convert to [[x1, y1], [x2, y2], ...]
        roi = [[s.points[i], s.points[i+1]] for i in range(0, len(s.points), 2)]
        rois.append(roi)
        final_occupied = 1 if s.occupied else 0
        occupancy.append(final_occupied)
        # ``original_occupied`` is the AI's prediction — the frontend
        # sends it inline so we don't have to re-query Occupancy
        # (which retention may have pruned). When the user didn't
        # change the answer, the original may be ``None`` (frontend
        # couldn't determine it); we still emit the parallel array
        # so consumers can index by position.
        was_occupied = (
            1 if s.original_occupied is True
            else 0 if s.original_occupied is False
            else None
        )
        original_occupancy.append(was_occupied)
        # A "correction" is any space where the user flipped the
        # answer. When the frontend couldn't tell us the original
        # (None), we skip the entry — we don't know if it was a
        # correction or not.
        if was_occupied is not None and was_occupied != final_occupied:
            corrections.append({
                "space_id": s.space_id,
                "was_occupied": bool(was_occupied),
                "now_occupied": bool(final_occupied),
                # Useful signal for the ML pipeline: was the AI too
                # eager (false positive) or too cautious (false
                # negative)? Cheap to compute inline; saves a
                # downstream pass over the data.
                "kind": "false_positive" if was_occupied else "false_negative",
            })

    metadata = {
        "file_name": img_filename,
        "app_version": config.APP_VERSION,
        "camera_id": data.camera_id,
        "scan_id": data.scan_id,
        "rois": rois,
        "occupancy": occupancy,
        # Parallel array to ``occupancy`` with the AI's original
        # prediction for each space. Entries may be ``None`` when
        # the frontend couldn't determine the original.
        "original_occupancy": original_occupancy,
        # Subset of spaces where the user flipped the AI's answer.
        # Used to compute false-positive / false-negative rates
        # without re-running inference over the dataset. Empty list
        # means the user confirmed every prediction (still useful
        # data — confirmed-correct is a strong training signal).
        "corrections": corrections,
        "submitted_by": user.username,
        "submitted_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }

    with open(os.path.join(annotations_dir, json_filename), "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info("Feedback received for camera {cid} (Scan {sid})", cid=data.camera_id, sid=data.scan_id)
    return {"message": "Feedback saved locally successfully."}



# Register feature APIs before the frontend catch-all route.
from .alerts import routes as alerts_routes
from .parking_layout import router as parking_layout_router
app.include_router(alerts_routes.router)
app.include_router(parking_layout_router)

# ---- Frontend static files --------------------------------------------------
# In PyInstaller frozen bundles, sys._MEIPASS points to the bundle root;
# use that to resolve frontend/dist.  Fall back to CWD-relative for dev.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    FRONTEND_DIST = os.path.join(sys._MEIPASS, "frontend", "dist")
else:
    FRONTEND_DIST = os.path.join("frontend", "dist")

if os.path.isdir(FRONTEND_DIST):
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    # 1. Ignore API calls (should have been caught by specific routes above)
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found")

    # 2. Serve static files from root if they exist (favicon, robots.txt, etc)
    # Resolve real paths and enforce containment so "/../.." tries can't
    # escape FRONTEND_DIST and read arbitrary host files (C1 audit fix).
    base = os.path.realpath(FRONTEND_DIST)
    static_path = os.path.realpath(os.path.join(base, full_path))
    if not (static_path == base or static_path.startswith(base + os.sep)):
        raise HTTPException(status_code=404, detail="Not found")
    if os.path.isfile(static_path):
        return FileResponse(static_path)
    
    # 3. Otherwise, return the SPA index.html for all frontend routes
    index_path = os.path.join(base, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    
    # Fallback if not built yet - return HTML for better UX in browser
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content="""
        <html>
            <body style="font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f5f5f5;">
                <div style="text-align: center; padding: 40px; background: white; border-radius: 8px; shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h1 style="color: #d32f2f;">Frontend Not Built</h1>
                    <p>The backend is running, but the frontend distribution files were not found.</p>
                    <p>Please run <code>npm run build</code> in the <code>frontend/</code> directory.</p>
                    <hr style="margin: 20px 0; border: 0; border-top: 1px solid #eee;">
                    <p style="font-size: 0.9em; color: #666;">If you are in development, use the Vite dev server (usually port 5173).</p>
                </div>
            </body>
        </html>
    """, status_code=404)
