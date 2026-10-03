from sqlalchemy.orm import Session
from sqlalchemy import func, Integer
from . import models, schemas
from . import auth as _auth
import json, datetime
from typing import List, Optional

def get_cameras(db: Session):
    return db.query(models.Camera).all()

def create_camera(cam: schemas.CameraCreate, db: Session):
    db_cam = models.Camera(**cam.model_dump())
    db.add(db_cam)
    db.commit()
    db.refresh(db_cam)
    return db_cam

def update_camera(db: Session, camera_id: int, cam: schemas.CameraCreate):
    from .stream_manager import stream_manager
    db_cam = db.query(models.Camera).filter_by(id=camera_id).first()
    if not db_cam:
        return None
    
    # Update all fields provided in the schema
    update_data = cam.model_dump()
    for key, value in update_data.items():
        if value is not None:
            setattr(db_cam, key, value)
    
    # If the camera was just disabled, kill its stream immediately
    if not db_cam.is_enabled:
        stream_manager.stop_stream(camera_id)
        
    db.commit()
    db.refresh(db_cam)
    return db_cam

def get_spaces(db: Session, camera_id: Optional[int] = None):
    """List spaces, optionally filtered by camera.

    ``camera_id=None`` (the default) returns all spaces — used by
    the alert rule form to populate the per-space picker across the
    entire system.
    """
    q = db.query(models.Space)
    if camera_id is not None:
        q = q.filter_by(camera_id=camera_id)
    return q.all()

def create_space(space: schemas.SpaceCreate, db: Session):
    db_space = models.Space(
        camera_id=space.camera_id,
        name=space.name,
        points=json.dumps(space.points)
    )
    db.add(db_space)
    db.commit()
    db.refresh(db_space)
    return db_space

def update_spaces(spaces: List[schemas.SpaceUpdate], db: Session):
    if not spaces:
        return []

    camera_id = spaces[0].camera_id
    
    # Get existing spaces for the camera
    existing_spaces = db.query(models.Space).filter_by(camera_id=camera_id).all()
    existing_space_map = {s.id: s for s in existing_spaces}
    
    incoming_space_ids = {s.id for s in spaces if s.id is not None}
    
    # 1. Delete spaces that are no longer present
    space_ids_to_delete = [sid for sid in existing_space_map if sid not in incoming_space_ids]
    if space_ids_to_delete:
        # Delete related occupancy/event rows first to avoid FK violations
        # (AlertStatePerSpace uses ON DELETE CASCADE and is handled by the DB)
        db.query(models.Occupancy).filter(
            models.Occupancy.space_id.in_(space_ids_to_delete)
        ).delete(synchronize_session=False)
        db.query(models.OccupancyEvent).filter(
            models.OccupancyEvent.space_id.in_(space_ids_to_delete)
        ).delete(synchronize_session=False)
        for sid in space_ids_to_delete:
            db.delete(existing_space_map[sid])
            
    # 2. Update existing spaces and create new ones
    for space_data in spaces:
        if space_data.id is not None and space_data.id in existing_space_map:
            # Update existing
            db_space = existing_space_map[space_data.id]
            db_space.name = space_data.name
            db_space.points = json.dumps(space_data.points)
        else:
            # Create new
            db_space = models.Space(
                camera_id=space_data.camera_id,
                name=space_data.name,
                points=json.dumps(space_data.points)
            )
            db.add(db_space)
            
    db.commit()
    
    # Return the updated list of all spaces for the camera
    return db.query(models.Space).filter_by(camera_id=camera_id).all()


def get_stats_aggregated(db: Session, camera_ids: List[int], start: str, end: str):
    """
    Returns occupancy statistics aggregated by minute.
    Returns: List of {timestamp, occupied, total} where occupied and total are summed across cameras for that minute.
    """
    start_dt = datetime.datetime.fromisoformat(start)
    end_dt = datetime.datetime.fromisoformat(end)

    # 1. Subquery to group by scan (unique timestamp per camera)
    scan_agg = db.query(
        models.CameraScan.camera_id.label('camera_id'),
        models.CameraScan.timestamp.label('ts'),
        func.count(models.Occupancy.id).label('total'),
        func.sum(func.cast(models.Occupancy.occupied, Integer)).label('occupied'),
        models.CameraScan.inference_speed.label('speed')
    ).join(models.Occupancy, models.Occupancy.scan_id == models.CameraScan.id)\
     .join(models.Space, models.Occupancy.space_id == models.Space.id)

    if camera_ids:
        scan_agg = scan_agg.filter(models.Space.camera_id.in_(camera_ids))
    
    scan_agg = scan_agg.filter(models.CameraScan.timestamp.between(start_dt, end_dt))\
                       .group_by(models.CameraScan.id, models.CameraScan.timestamp).subquery()

    # 2. Average per camera per minute (if a single camera has multiple scans in the same minute)
    camera_minute_agg = db.query(
        scan_agg.c.camera_id.label('camera_id'),
        func.strftime('%Y-%m-%dT%H:%M:00', scan_agg.c.ts).label('minute'),
        func.avg(scan_agg.c.occupied).label('avg_occupied'),
        func.avg(scan_agg.c.total).label('avg_total'),
        func.avg(scan_agg.c.speed).label('avg_speed')
    ).group_by(scan_agg.c.camera_id, func.strftime('%Y-%m-%dT%H:%M:00', scan_agg.c.ts)).subquery()

    # 3. Sum counts across all cameras for each minute
    minute_agg = db.query(
        camera_minute_agg.c.minute.label('minute'),
        func.sum(camera_minute_agg.c.avg_occupied).label('sum_occupied'),
        func.sum(camera_minute_agg.c.avg_total).label('sum_total'),
        func.avg(camera_minute_agg.c.avg_speed).label('avg_speed')
    ).group_by(camera_minute_agg.c.minute)\
     .order_by('minute').all()

    return [
        {
            "timestamp": row.minute,
            "occupied": float(row.sum_occupied),
            "total": float(row.sum_total),
            "speed": float(row.avg_speed) if row.avg_speed is not None else 0.0
        }
        for row in minute_agg
    ]


def get_stats_hourly(db: Session, camera_ids: List[int], start: str, end: str):
    start_dt = datetime.datetime.fromisoformat(start)
    end_dt = datetime.datetime.fromisoformat(end)
    
    # Aggregate across cameras for each hour
    query = db.query(
        models.OccupancyHourly.timestamp,
        func.sum(models.OccupancyHourly.avg_occupied).label('occupied'),
        func.sum(models.OccupancyHourly.total_spaces).label('total'),
        func.avg(models.OccupancyHourly.avg_inference_speed).label('speed')
    )
    
    if camera_ids:
        query = query.filter(models.OccupancyHourly.camera_id.in_(camera_ids))
        
    rows = query.filter(
        models.OccupancyHourly.timestamp.between(start_dt, end_dt)
    ).group_by(models.OccupancyHourly.timestamp)\
     .order_by(models.OccupancyHourly.timestamp.asc()).all()
    
    return [
        {
            "timestamp": row.timestamp.isoformat(),
            "occupied": float(row.occupied),
            "total": float(row.total),
            "speed": float(row.speed) if row.speed is not None else 0.0
        }
        for row in rows
    ]

def get_setting(db: Session, key: str):
    return db.query(models.Setting).filter_by(key=key).first()

def update_setting(db: Session, key: str, value: str):
    db_setting = db.query(models.Setting).filter_by(key=key).first()
    if db_setting:
        db_setting.value = value
    else:
        db_setting = models.Setting(key=key, value=value)
        db.add(db_setting)
    db.commit()
    db.refresh(db_setting)
    return db_setting

def _user_with_list_permissions(user: models.User):
    if not user: return None
    user_dict = {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "permissions": _auth.parse_permissions(user)
    }
    return user_dict

def get_users(db: Session):
    users = db.query(models.User).all()
    return [_user_with_list_permissions(u) for u in users]

def create_user(user: schemas.UserCreate, db: Session):
    from .auth import hash_password
    db_user = models.User(
        username=user.username,
        password_hash=hash_password(user.password),
        is_admin=user.is_admin,
        permissions=json.dumps(user.permissions),
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return _user_with_list_permissions(db_user)

def update_user(db: Session, user_id: int, user_update: schemas.UserUpdate):
    from .auth import hash_password
    db_user = db.query(models.User).filter_by(id=user_id).first()
    if not db_user:
        return None

    if user_update.password:
        db_user.password_hash = hash_password(user_update.password)

    if user_update.is_admin is not None:
        # Check "at least one admin" rule
        if user_update.is_admin == False and db_user.is_admin == True:
            admins_count = db.query(models.User).filter_by(is_admin=True).count()
            if admins_count <= 1:
                raise Exception("Cannot demote the last administrator")
        db_user.is_admin = user_update.is_admin

    if user_update.permissions is not None:
        db_user.permissions = json.dumps(user_update.permissions)
    
    db.commit()
    db.refresh(db_user)
    return _user_with_list_permissions(db_user)

def delete_user(db: Session, user_id: int):
    db_user = db.query(models.User).filter_by(id=user_id).first()
    if not db_user:
        return False
    
    # Check "at least one admin" rule
    if db_user.is_admin:
        admins_count = db.query(models.User).filter_by(is_admin=True).count()
        if admins_count <= 1:
            raise Exception("Cannot delete last admin user")
            
    db.delete(db_user)
    db.commit()
    return True

def delete_camera(db: Session, camera_id: int):
    db_cam = db.query(models.Camera).filter_by(id=camera_id).first()
    if db_cam:
        space_ids = [s.id for s in db.query(models.Space.id).filter_by(camera_id=camera_id).all()]
        if space_ids:
            # Delete child events and occupancies tied to spaces first
            db.query(models.OccupancyEvent).filter(models.OccupancyEvent.space_id.in_(space_ids)).delete(synchronize_session=False)
            db.query(models.Occupancy).filter(models.Occupancy.space_id.in_(space_ids)).delete(synchronize_session=False)
            db.query(models.Space).filter(models.Space.id.in_(space_ids)).delete(synchronize_session=False)

        scan_ids = [s.id for s in db.query(models.CameraScan.id).filter_by(camera_id=camera_id).all()]
        if scan_ids:
            db.query(models.Occupancy).filter(models.Occupancy.scan_id.in_(scan_ids)).delete(synchronize_session=False)
            db.query(models.CameraScan).filter(models.CameraScan.id.in_(scan_ids)).delete(synchronize_session=False)

        db.query(models.OccupancyHourly).filter_by(camera_id=camera_id).delete(synchronize_session=False)
        db.query(models.CameraGroupMembership).filter_by(camera_id=camera_id).delete(synchronize_session=False)
        db.delete(db_cam)
        db.commit()
        return True
    return False


def get_inference_stats(db: Session, camera_id: int, limit: int = 100):
    rows = db.query(models.CameraScan.timestamp, models.CameraScan.inference_speed)\
             .filter(models.CameraScan.camera_id == camera_id, models.CameraScan.inference_speed != None)\
             .order_by(models.CameraScan.timestamp.desc())\
             .limit(limit).all()
    
    return [
        {"timestamp": r.timestamp.isoformat(), "speed": r.inference_speed}
        for r in reversed(rows) # Return in chronological order
    ]

def get_overall_inference_stats(db: Session, limit: int = 100):
    if limit == 1:
        # For the dashboard footer: Average the most recent scan from every camera
        latest_scan_ids = db.query(func.max(models.CameraScan.id)).group_by(models.CameraScan.camera_id)
        avg_speed = db.query(func.avg(models.CameraScan.inference_speed))\
                      .filter(models.CameraScan.id.in_(latest_scan_ids))\
                      .scalar()
        
        return [
            {"timestamp": datetime.datetime.now(datetime.UTC).isoformat(), "speed": avg_speed or 0.0}
        ]

    # For historical time-series (if needed): Group by timestamp and average
    rows = db.query(models.CameraScan.timestamp, func.avg(models.CameraScan.inference_speed).label('avg_speed'))\
             .filter(models.CameraScan.inference_speed != None)\
             .group_by(models.CameraScan.timestamp)\
             .order_by(models.CameraScan.timestamp.desc())\
             .limit(limit).all()
    
    return [
        {"timestamp": r.timestamp.isoformat(), "speed": r.avg_speed}
        for r in reversed(rows)
    ]

def get_events(db: Session, space_id: int, limit: int = 50):
    return db.query(models.OccupancyEvent)\
             .filter_by(space_id=space_id)\
             .order_by(models.OccupancyEvent.timestamp.desc())\
             .limit(limit)\
             .all()

def _parse_datetime(dt_str: str) -> datetime.datetime:
    s = dt_str.replace('Z', '+00:00') if dt_str.endswith('Z') else dt_str
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt

def get_scans_by_date(db: Session, camera_id: int, date_str: str):
    start_dt = _parse_datetime(date_str)
    end_dt = start_dt + datetime.timedelta(days=1)

    scans = db.query(models.CameraScan.id, models.CameraScan.timestamp, models.CameraScan.has_image)\
              .filter(
                  models.CameraScan.camera_id == camera_id,
                  models.CameraScan.timestamp >= start_dt,
                  models.CameraScan.timestamp < end_dt
              )\
              .order_by(models.CameraScan.timestamp.asc()).all()

    return [
        {"id": s.id, "timestamp": s.timestamp.isoformat(), "has_image": s.has_image}
        for s in scans
    ]

def get_scans_range(db: Session, camera_id: int, start: str, end: str):
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)

    scans = db.query(models.CameraScan.id, models.CameraScan.timestamp, models.CameraScan.has_image)\
              .filter(
                  models.CameraScan.camera_id == camera_id,
                  models.CameraScan.timestamp >= start_dt,
                  models.CameraScan.timestamp < end_dt
              )\
              .order_by(models.CameraScan.timestamp.asc()).all()

    return [
        {"id": s.id, "timestamp": s.timestamp.isoformat(), "has_image": s.has_image}
        for s in scans
    ]

def get_scan_details(db: Session, scan_id: int):
    scan = db.query(models.CameraScan).filter_by(id=scan_id).first()
    if not scan:
        return None
    
    occupancies = db.query(models.Occupancy.space_id, models.Occupancy.occupied)\
                    .filter_by(scan_id=scan_id).all()
    
    return {
        "id": scan.id,
        "timestamp": scan.timestamp.isoformat(),
        "camera_id": scan.camera_id,
        "inference_speed": scan.inference_speed,
        "occupancy": {o.space_id: o.occupied for o in occupancies}
    }

def get_space_events_range(db: Session, space_id: int, start: str, end: str):
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)

    events = db.query(models.OccupancyEvent)\
               .filter(
                   models.OccupancyEvent.space_id == space_id,
                   models.OccupancyEvent.timestamp >= start_dt,
                   models.OccupancyEvent.timestamp < end_dt
               )\
               .order_by(models.OccupancyEvent.timestamp.desc()).all()

    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "event_type": e.event_type,
            "confidence": e.confidence,
            "has_crop": e.has_crop
        }
        for e in events
    ]

def get_api_keys(db: Session):
    keys = db.query(models.APIKey).all()
    # Ensure creator relationship is loaded if needed, or handle in response
    return keys

def create_api_key(db: Session, user_id: int, name: str):
    from . import auth
    full_key = auth.generate_api_key()
    prefix = full_key[:4]
    hashed = auth.hash_key(full_key)
    
    db_key = models.APIKey(
        creator_id=user_id,
        name=name,
        key_prefix=prefix,
        hashed_key=hashed
    )
    db.add(db_key)
    db.commit()
    db.refresh(db_key)
    
    # Attach full key so it can be returned once
    setattr(db_key, 'full_key', full_key)
    return db_key

def delete_api_key(db: Session, key_id: int):
    db_key = db.query(models.APIKey).filter_by(id=key_id).first()
    if db_key:
        db.delete(db_key)
        db.commit()
        return True
    return False
