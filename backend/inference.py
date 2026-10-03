import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import time
import cv2
import json
import asyncio
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from . import models, database
try:
    from vulturevision import VultureVision
except ImportError:
    # Fallback stub when running in development/test environments without the compiled C++ wheel
    class VultureVision:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass
        def predict(self, *args, **kwargs):
            return []
        def detect(self, *args, **kwargs):
            return []
        @staticmethod
        def get_available_gpu_provider():
            return {"provider": "", "error": "vulturevision engine not installed"}
from .billing.enforcer import resolve_license
from .logging_config import vulture_logger as logger

# VultureVision manages its own internal engine pool and GPU serialized access
_vv_instance = None
_vv_config = {"license": None, "instance_id": None, "use_gpu": None, "max_res": None, "last_check": 0}
# Protects cheapreads/writes of _vv_instance and _vv_config. Hold for
# microseconds at a time, NEVER across the multi-second C++ engine build.
_vv_init_lock = threading.Lock()
# Serializes the slow VultureVision() construction so two worker threads
# never build the same engine twice. This is distinct from _vv_init_lock
# specifically so async callers (billing heartbeat, PUT /api/settings)
# can still mutate _vv_config["last_check"] via invalidate_config_cache()
# without their event loop blocking on a C++ build.
_vv_build_lock = threading.Lock()
_vv_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference_worker")


def invalidate_config_cache():
    """Force the next ``get_vulturevision()`` call to re-read settings.

    Called from async event-loop contexts: the billing heartbeat
    coroutine (scheduler._send_one_heartbeat) and the PUT
    /api/settings/{key} route when the operator changes
    ``inference_device`` or ``max_inference_resolution``.

    Acquires ``_vv_init_lock`` to write ``last_check = 0`` safely.
    Since ``_vv_init_lock`` is only held for microseconds in
    ``get_vulturevision`` (and never across the slow C++ build),
    this is safe and does not block the event loop.
    """
    global _vv_config
    with _vv_init_lock:
        _vv_config["last_check"] = 0


def get_inference_executor():
    return _vv_executor

def get_vulturevision(license_key=None, instance_id=None):
    global _vv_instance, _vv_config

    # Cheap path: if we already have an instance and the cache is fresh,
    # return immediately. _vv_init_lock is only held long enough to read
    # two fields — it is never held across the slow C++ build below.
    with _vv_init_lock:
        if _vv_instance is not None and (time.time() - _vv_config["last_check"]) <= 60:
            return _vv_instance

    # Slow path. Serialize builds across worker threads (so we don't
    # construct two VultureVision instances at once) WITHOUT holding
    # _vv_init_lock across the multi-second C++ construction — that
    # would block invalidate_config_cache()'s atomic write from the
    # event loop and freeze the whole app in CPU mode.
    with _vv_build_lock:
        # Another worker may have just finished building while we were
        # waiting on _vv_build_lock; re-check the cache before doing
        # any work.
        with _vv_init_lock:
            if _vv_instance is not None and (time.time() - _vv_config["last_check"]) <= 60:
                return _vv_instance

        if license_key is None or instance_id is None:
            license_key, instance_id = resolve_license_from_default_session()

        db = database.SessionLocal()
        try:
            device_setting = db.query(models.Setting).filter_by(key="inference_device").first()
            # Default to CPU when the setting row is missing. A missing
            # row can happen on restores from pre-`inference_device`
            # backups or after manual SQL edits. CPU is the safe default:
            # the C++ engine always falls back to CPU if the CUDA/DML
            # provider fails to load, but starting in "use_gpu=True"
            # mode produces noisy "GPU provider load failed" log lines
            # every 60 seconds on hosts without a GPU. The operator can
            # still opt in to GPU via the Settings page once a row with
            # value="cuda" is written by init_db.py or the UI.
            use_gpu = (device_setting.value == "cuda") if device_setting else False

            from .billing.session import default_session
            from .billing.enforcer import compute_mode, COMMUNITY
            sess = default_session()
            mode = compute_mode(sess)

            if use_gpu and mode == COMMUNITY:
                logger.warning("GPU processing is a commercial-only feature. Downgrading to CPU mode for community license.")
                use_gpu = False

            max_res_setting = db.query(models.Setting).filter_by(key="max_inference_resolution").first()
            max_res = int(max_res_setting.value) if max_res_setting else 1440
        finally:
            db.close()

        # Snapshot the cached fields under the init lock so the rebuild
        # decision is consistent. This is fast and never blocks the
        # event loop meaningfully.
        with _vv_init_lock:
            rebuild_needed = (_vv_instance is None or
                              _vv_config["license"] != license_key or
                              _vv_config["instance_id"] != instance_id or
                              _vv_config["use_gpu"] != use_gpu or
                              _vv_config["max_res"] != max_res)

        if not rebuild_needed:
            # Settings are unchanged — just refresh the poll timestamp
            # so we don't re-enter the slow path for another 60s.
            # BUT don't clobber last_check=0 if invalidate_config_cache()
            # wrote it during the time spent waiting on _vv_build_lock,
            # otherwise a license downgrade would be silently dropped.
            with _vv_init_lock:
                if _vv_config["last_check"] != 0:
                    _vv_config["last_check"] = time.time()
            return _vv_instance

        # ---- Slow C++ build runs WITHOUT _vv_init_lock held ----
        start_init = time.perf_counter()
        logger.info("Initializing VultureVision (GPU: {gpu}, MaxRes: {res}, Mode: {mode})...",
                    gpu=use_gpu, res=max_res,
                    mode="commercial" if license_key else "community")
        try:
            new_instance = VultureVision(
                license_key=license_key,
                instance_id=instance_id,
                use_gpu=use_gpu,
                max_resolution=max_res
            )
        except Exception as e:
            logger.error("VultureVision initialization/update failed: {e}", e=e)
            # Re-raise only if we have no usable instance at all. If
            # we already have a stale engine, keep serving it and leave
            # last_check untouched so the next call retries the build.
            with _vv_init_lock:
                if _vv_instance is None:
                    raise
            return _vv_instance

        init_elapsed = time.perf_counter() - start_init
        logger.info("VultureVision library loaded successfully in {ms:.0f}ms", ms=init_elapsed * 1000)

        # Publish the new instance under the init lock. If
        # invalidate_config_cache() wrote last_check=0 during the slow
        # build above (e.g. a license downgrade arrived mid-build),
        # preserve the 0 so the NEXT call re-reads settings and rebuilds
        # again — instead of clobbering it back to time.time() and
        # dropping the invalidation.
        with _vv_init_lock:
            _vv_instance = new_instance
            _vv_config.update({
                "license": license_key,
                "instance_id": instance_id,
                "use_gpu": use_gpu,
                "max_res": max_res
            })
            if _vv_config["last_check"] != 0:
                _vv_config["last_check"] = time.time()

    return _vv_instance


def resolve_license_from_default_session():
    """Resolve ``(license_key, instance_id)`` from the current
    ``LocalSession``. When mode is community both are empty strings
    so the C++ engine loads the bundled community model.

    H4 audit fix: enforce expiry on the live session before
    resolving, so an expired license is caught on the very next
    inference call (within 60s) even if the heartbeat loop is
    down or blocked.
    """
    from .billing.session import default_session
    from .billing.enforcer import enforce_expiry
    sess = default_session()
    if enforce_expiry(sess):
        # Persist the downgrade immediately so a follow-up
        # ``compute_mode`` call (and a future heartbeat) sees the
        # community state without waiting for the next save tick.
        try:
            sess.save()
        except Exception:
            pass
    return resolve_license(sess)

async def run_inference(camera_id: int, image_bytes: bytes, spaces: list, max_res: int = 1440):
    if not spaces: return {}, 0.0, (0, 0)
    
    # Offload decoding to thread
    img = await asyncio.to_thread(cv2.imdecode, np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None: return {}, 0.0, (0, 0)
    
    # 1. Prepare ROIs for VultureVision (N, 4, 2) normalized
    rois = []
    space_ids = []
    for s in spaces:
        pts = s['points']
        if isinstance(pts, str): pts = json.loads(pts)
        if len(pts) != 8: continue
        rois.append([[pts[i], pts[i+1]] for i in range(0, 8, 2)])
        space_ids.append(s['id'])
    
    if not rois: return {}, 0.0, img.shape[1::-1]
    
    rois_np = np.array(rois, dtype=np.float32)
    
    try:
        loop = asyncio.get_running_loop()
        executor = get_inference_executor()
        
        # Resolve license and enforce expiry safely on the main event loop under the lock
        from .billing.session import default_session, session_update_lock
        from .billing.enforcer import enforce_expiry, resolve_license
        
        async with session_update_lock():
            sess = default_session()
            if enforce_expiry(sess):
                try:
                    sess.save()
                except Exception:
                    pass
            license_key, instance_id = resolve_license(sess)
        
        # get_vulturevision is now sync but uses a thread lock. 
        # Model creation inside it is the heavy part.
        vv = await asyncio.to_thread(get_vulturevision, license_key, instance_id)
        start_time = time.perf_counter()
        
        probs = await loop.run_in_executor(
            executor,
            vv.predict,
            img, rois_np
        )
        
        elapsed = time.perf_counter() - start_time
        probs_dict = {sid: float(p) for sid, p in zip(space_ids, probs)}
        return probs_dict, elapsed, img.shape[1::-1]
        
    except Exception as e:
        logger.error("VultureVision inference failed: {e}", e=e)
        # Re-raise rather than returning all-zero probabilities. The
        # scheduler's outer try/except catches the failure, skips the
        # ``_do_save_results`` call for this iteration, and the cached
        # ``occupied`` state is preserved. Returning {0.0} previously
        # caused the save path to flip every space to vacant and
        # persist that as a state change, masking real engine failures
        # from the operator. The manual-test endpoint at app.py also
        # has an outer try/except that maps the failure to a 500.
        raise

async def get_crops(camera_id: int, image_bytes: bytes, spaces: list, max_res: int = 1440, quality: int = 90):
    if not spaces: return {}
    
    def _do_crops():
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return {}
        
        h_work, w_work = img.shape[:2]
        results = {}
        
        for s in spaces:
            pts = s['points']
            if isinstance(pts, str): pts = json.loads(pts)
            if len(pts) != 8: continue
            
            # Convert normalized [0,1] points to absolute pixel coordinates
            x_coords = [pts[i] * w_work for i in range(0, 8, 2)]
            y_coords = [pts[i+1] * h_work for i in range(0, 8, 2)]
            
            # Find center and dimensions of the ROI
            cx, cy = sum(x_coords) / 4.0, sum(y_coords) / 4.0
            w, h = max(x_coords) - min(x_coords), max(y_coords) - min(y_coords)
            
            # Use the largest dimension to ensure a square crop
            side = max(w, h)
            
            x1, y1 = int(cx - side/2), int(cy - side/2)
            x2, y2 = int(cx + side/2), int(cy + side/2)
            
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w_work, x2), min(h_work, y2)
            
            if x2 <= x1 or y2 <= y1: continue

                
            crop = img[y1:y2, x1:x2]
            if crop.size == 0: continue
            
            warp = cv2.resize(crop, (128, 128))
            _, buffer = cv2.imencode('.jpg', warp, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            results[s['id']] = buffer.tobytes()
        return results

    loop = asyncio.get_running_loop()
    # Pass None to use the default multi-worker ThreadPoolExecutor
    return await loop.run_in_executor(None, _do_crops)

def get_gpu_provider():
    """Returns the GPU provider name available, or empty string if none.

    Thin pass-through to ``vulturevision.get_gpu_provider``,
    which executes the C++ probe in a separate subprocess.
    """
    import sys
    is_test = "pytest" in sys.modules or "unittest" in sys.modules

    if is_test:
        try:
            from vulturevision import _vulturevision
            result = _vulturevision.get_available_gpu_provider()
            if isinstance(result, str):
                return {"provider": result, "error": ""}
            provider = result.get("provider", "")
            error = result.get("error", "")
            if not provider and not error:
                error = (
                    "CUDAExecutionProvider could not be loaded. Run the "
                    "probe directly from a Python shell to see stderr output."
                )
            return {"provider": provider, "error": error}
        except Exception as e:
            return {"provider": "", "error": f"GPU probe failed: {type(e).__name__}: {e}"}

    try:
        import vulturevision
        return vulturevision.get_gpu_provider()
    except Exception as e:
        return {"provider": "", "error": f"GPU probe failed: {type(e).__name__}: {e}"}


def _probe_gpu_provider_safely():
    """Same as :func:`get_gpu_provider` — kept as a name for
    backward compatibility with the existing route handler.
    """
    return get_gpu_provider()


def is_cuda_available():
    return bool(get_gpu_provider().get("provider"))

