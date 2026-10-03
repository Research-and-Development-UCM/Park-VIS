"""Display layouts are independent of camera detection polygons."""
import base64
import json
import secrets
import re
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from . import auth, database, models

router = APIRouter(prefix="/api/parking-layout", tags=["parking-layout"])
LAYOUT_KEY = "parking_display_layout_v1"
SHARE_KEY = "parking_display_view_key"


class LayoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["stall", "road", "label", "entry"]
    name: str = Field(default="", max_length=80)
    x: float = Field(ge=0, le=10000)
    y: float = Field(ge=0, le=10000)
    width: float = Field(ge=8, le=2000)
    height: float = Field(ge=8, le=2000)
    angle: float = Field(default=0, ge=-360, le=360)
    locked: bool = Field(default=False, strict=True)
    space_id: int | None = Field(default=None, ge=1)


class ParkingLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    name: str = Field(default="My parking lot", min_length=1, max_length=80)
    width: int = Field(default=1200, ge=200, le=10000)
    height: int = Field(default=800, ge=200, le=10000)
    background: str = Field(default="", max_length=4_000_000)
    items: list[LayoutItem] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_layout(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Layout object IDs must be unique")
        links = [i.space_id for i in self.items if i.kind == "stall" and i.space_id]
        if len(set(links)) != len(links):
            raise ValueError("Each camera space can be linked to only one stall")
        for item in self.items:
            if item.x > self.width or item.y > self.height:
                raise ValueError("Objects must be inside the layout")
            if item.kind != "stall" and item.space_id is not None:
                raise ValueError("Only stalls can be linked to camera spaces")
        if self.background:
            prefixes = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
            if not self.background.startswith(prefixes):
                raise ValueError("Background must be an uploaded PNG, JPEG, or WebP")
            try:
                data = base64.b64decode(self.background.split(",", 1)[1], validate=True)
                valid = (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff") or
                         (data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
                if not valid:
                    raise ValueError("Unsupported image contents")
            except Exception as exc:
                raise ValueError("Invalid background image") from exc
        return self


def authenticated(user=Depends(auth.get_auth_user)):
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


def editor(user=Depends(authenticated)):
    if not auth.check_permission_direct(user, "manage_cameras"):
        raise HTTPException(403, "Missing required permission: manage_cameras")
    return user


def scoped_key(key, lot):
    return f"{key}:{lot}" if lot else key


def lot_cameras(db, lot):
    if lot is None:
        return None
    if not re.fullmatch(r"(group|camera):[1-9][0-9]*", lot):
        raise HTTPException(422, "Choose a valid parking lot")
    kind, identifier = lot.split(":")
    identifier = int(identifier)
    if kind == "camera":
        if db.get(models.Camera, identifier) is None:
            raise HTTPException(404, "Parking lot camera not found")
        return {identifier}
    if db.get(models.CameraGroup, identifier) is None:
        raise HTTPException(404, "Parking lot group not found")
    return {m.camera_id for m in db.query(models.CameraGroupMembership).filter_by(camera_group_id=identifier).all()}


def read_layout(db, lot=None):
    record = db.get(models.Setting, scoped_key(LAYOUT_KEY, lot))
    return json.loads(record.value) if record else None


def viewer_lot(db, view_key):
    records = db.query(models.Setting).filter(
        (models.Setting.key == SHARE_KEY) | models.Setting.key.startswith(SHARE_KEY + ":")
    ).all()
    for record in records:
        if record.value and secrets.compare_digest(record.value, view_key):
            lot = record.key[len(SHARE_KEY)+1:] if record.key != SHARE_KEY else None
            lot_cameras(db, lot)
            return lot
    raise HTTPException(404, "Viewer not found")


def store(db, key, value):
    record = db.get(models.Setting, key)
    if record:
        record.value = value
    else:
        db.add(models.Setting(key=key, value=value))
    db.commit()


@router.get("")
def get_layout(lot: str | None = None, db: Session = Depends(database.get_db), user=Depends(authenticated)):
    camera_ids = lot_cameras(db, lot)
    layout = read_layout(db, lot)
    if layout and camera_ids is not None:
        valid = {s.id for s in db.query(models.Space).filter(models.Space.camera_id.in_(camera_ids)).all()}
        for item in layout["items"]:
            if item["space_id"] and item["space_id"] not in valid:
                item["space_id"] = None
    return {"layout": layout}


@router.put("")
def save_layout(layout: ParkingLayout, lot: str | None = None, db: Session = Depends(database.get_db), user=Depends(editor)):
    camera_ids = lot_cameras(db, lot)
    links = {i.space_id for i in layout.items if i.space_id}
    if links:
        query = db.query(models.Space).filter(models.Space.id.in_(links))
        if camera_ids is not None:
            query = query.filter(models.Space.camera_id.in_(camera_ids))
        existing = {s.id for s in query.all()}
        if links != existing:
            raise HTTPException(422, "Linked spaces must belong to the selected parking lot. Update their links first.")
    store(db, scoped_key(LAYOUT_KEY, lot), layout.model_dump_json())
    return {"layout": layout.model_dump()}


@router.post("/share")
def enable_view(lot: str | None = None, db: Session = Depends(database.get_db), user=Depends(editor)):
    lot_cameras(db, lot)
    if read_layout(db, lot) is None:
        raise HTTPException(409, "Save the layout before creating a live viewer")
    key = scoped_key(SHARE_KEY, lot)
    record = db.get(models.Setting, key)
    if not record or not record.value:
        store(db, key, secrets.token_urlsafe(32))
    return {"view_key": db.get(models.Setting, key).value}


@router.delete("/share")
def disable_view(lot: str | None = None, db: Session = Depends(database.get_db), user=Depends(editor)):
    lot_cameras(db, lot)
    store(db, scoped_key(SHARE_KEY, lot), "")
    return {"disabled": True}


@router.get("/view/{view_key}")
def public_view(view_key: str, response: Response, db: Session = Depends(database.get_db)):
    lot = viewer_lot(db, view_key)
    layout = get_layout(lot, db, None)["layout"]
    if layout is None:
        raise HTTPException(404, "Layout not found")
    from . import scheduler
    ids = {item["space_id"] for item in layout["items"] if item["space_id"]}
    spaces = db.query(models.Space).filter(models.Space.id.in_(ids)).all() if ids else []
    states = {}
    for space in spaces:
        state = scheduler.get_latest_state(space.camera_id)
        info = state.get(space.id, state.get(str(space.id), {}))
        states[str(space.id)] = {
            "occupied": info.get("occupied"),
            "updated_at": state.get("metadata", {}).get("timestamp"),
        }
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return {"layout": layout, "states": states}


@router.get("/embed/{view_key}")
def embed_view(view_key: str, db: Session = Depends(database.get_db)):
    viewer_lot(db, view_key)
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    # Prefer the freshly generated development artifact over an older build.
    candidates = [root / "frontend/public/parking-view.html", root / "frontend/dist/parking-view.html"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise HTTPException(503, "Build the frontend parking viewer first")
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
