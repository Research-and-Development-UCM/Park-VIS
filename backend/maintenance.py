import datetime
import os
import shutil

from sqlalchemy import func
from sqlalchemy.orm import Session
from . import models, database, config
from .logging_config import vulture_logger as logger

BATCH_SIZE = 50000

# Emergency disk threshold — when the filesystem hosting the data
# directory has less than this many bytes free the maintenance code
# starts evicting the oldest LMDB chunks regardless of retention.
_EMERGENCY_DISK_MIN_BYTES = 1500 * 1024 * 1024  # 1500 MB


def run_maintenance(db: Session):
    """
    Performs data aggregation and purges old data/blobs based on settings.
    This logic is shared between the background scheduler and the CLI.
    """
    logger.info("Starting data maintenance and pruning...")

    now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    # Fetch retention settings
    img_hours = _get_setting(db, "retention_images_hours", 24)
    evt_days = _get_setting(db, "retention_events_days", 30)
    raw_days = _get_setting(db, "retention_raw_data_days", 7)
    hourly_days = _get_setting(db, "retention_hourly_data_days", 365)

    # 1. Aggregate data older than 1 hour into OccupancyHourly
    _aggregate_hourly(db, now_utc)

    # 2. Cleanup: Remove raw data older than M days
    raw_cutoff = now_utc - datetime.timedelta(days=raw_days)
    _delete_raw_data(db, raw_cutoff)

    # 3. Cleanup: Remove occupancy events older than K days
    evt_cutoff = now_utc - datetime.timedelta(days=evt_days)
    _delete_old_events(db, evt_cutoff)

    # 4. Cleanup: Remove hourly data older than X days
    hourly_cutoff = now_utc - datetime.timedelta(days=hourly_days)
    _delete_batched(db, models.OccupancyHourly, models.OccupancyHourly.timestamp, hourly_cutoff)

    # 5. Evict LMDB chunks whose every entry is past the retention window.
    #    Walk oldest chunks first; once we find one with an in-range entry
    #    all newer chunks are also in-range, so we stop.
    _evict_expired_chunks(database.snapshot_db, retention_hours=img_hours)
    _evict_expired_chunks(database.crop_db, retention_hours=evt_days * 24)


    # 6. Emergency disk-space guard — if the data directory is critically
    #    low on free space, evict the oldest snapshot chunk (which is the
    #    biggest consumer) regardless of retention.
    _emergency_disk_evict(database.snapshot_db)
    _emergency_disk_evict(database.crop_db)

    logger.info("Cleanup complete. Retention: Events={evt}d, Raw={raw}d, Hourly={hr}d",
                evt=evt_days, raw=raw_days, hr=hourly_days)


def _get_setting(db, key, default):
    s = db.query(models.Setting).filter_by(key=key).first()
    return int(s.value) if s else default


def _delete_batched(db, model, timestamp_col, cutoff):
    while True:
        batch = db.query(model.id).filter(timestamp_col < cutoff).limit(BATCH_SIZE).all()
        if not batch:
            break
        ids = [r[0] for r in batch]
        db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def _aggregate_hourly(db, now_utc):
    last_agg = db.query(func.max(models.OccupancyHourly.timestamp)).scalar()
    current_agg_hour = (last_agg + datetime.timedelta(hours=1)) if last_agg else (now_utc - datetime.timedelta(days=30)).replace(
        minute=0, second=0, microsecond=0)
    stop_hour = now_utc.replace(minute=0, second=0, microsecond=0)

    while current_agg_hour < stop_hour:
        next_hour = current_agg_hour + datetime.timedelta(hours=1)
        cameras = db.query(models.Camera).all()
        for cam in cameras:
            scans = db.query(models.CameraScan).filter(
                models.CameraScan.camera_id == cam.id,
                models.CameraScan.timestamp >= current_agg_hour,
                models.CameraScan.timestamp < next_hour
            ).all()
            if not scans:
                continue

            scan_ids = [s.id for s in scans]
            scan_totals = db.query(
                models.Occupancy.scan_id,
                func.count(models.Occupancy.id).label('occupied_count')
            ).filter(
                models.Occupancy.scan_id.in_(scan_ids),
                models.Occupancy.occupied == True
            ).group_by(models.Occupancy.scan_id).all()

            total_spaces = db.query(models.Space).filter_by(camera_id=cam.id).count()

            avg_speed = db.query(func.avg(models.CameraScan.inference_speed))\
                .filter(models.CameraScan.id.in_(scan_ids), models.CameraScan.inference_speed != None)\
                .scalar()

            # ``scan_totals`` is the GROUP BY of *occupied* rows — it is
            # empty when every space was vacant for the whole hour,
            # which the previous ``if scan_totals:`` skipped. That left
            # a hole in the metrics chart for every quiet hour. Write
            # the row whenever the camera actually ran scans, even with
            # zero occupancy. ``scans == []`` (camera offline / disabled
            # the whole hour) is still skipped above to keep those hours
            # out of the chart entirely.
            if scan_ids:
                occupied_total = sum(s.occupied_count for s in scan_totals)
                avg_occ = occupied_total / len(scan_ids)
                hourly_entry = models.OccupancyHourly(
                    camera_id=cam.id,
                    timestamp=current_agg_hour,
                    avg_occupied=round(avg_occ),
                    total_spaces=total_spaces,
                    avg_inference_speed=avg_speed
                )
                db.add(hourly_entry)
        current_agg_hour = next_hour
    db.commit()


def _delete_raw_data(db, cutoff):
    while True:
        batch = db.query(models.CameraScan.id).filter(
            models.CameraScan.timestamp < cutoff
        ).limit(BATCH_SIZE).all()
        if not batch:
            break
        ids = [r[0] for r in batch]
        db.query(models.Occupancy).filter(
            models.Occupancy.scan_id.in_(ids)
        ).delete(synchronize_session=False)
        db.query(models.CameraScan).filter(
            models.CameraScan.id.in_(ids)
        ).delete(synchronize_session=False)
        db.commit()


def _delete_old_events(db, cutoff):
    _delete_batched(db, models.OccupancyEvent, models.OccupancyEvent.timestamp, cutoff)


def _evict_expired_chunks(store, retention_hours: int):
    """Delete entire LMDB chunks whose every entry is past the retention window.

    Each chunk directly stores its last_event_time internally within the LMDB file,
    making eviction independent of OS file modification times and external tools.
    """
    if retention_hours <= 0:
        return
    now_ts = int(datetime.datetime.now(datetime.UTC).timestamp())
    cutoff_ts = now_ts - (retention_hours * 3600)

    deleted_any = False
    while store.num_chunks > 1:
        chunks_info = store.chunks
        if len(chunks_info) < 2:
            break
        oldest = chunks_info[0]

        last_ts = oldest.get("last_event_time")
        if last_ts is not None and last_ts > cutoff_ts:
            break  # Chunk still contains entries within retention window

        store.delete_oldest()
        deleted_any = True


    if deleted_any:
        logger.info(
            "Evicted expired LMDB chunks ({store}) past retention of {retention_hours}h",
            store=store.directory, retention_hours=retention_hours,
        )



def _emergency_disk_evict(store):
    """If the data directory's filesystem is critically low on free space,
    evict the oldest chunk (if more than one exists)."""
    stat = shutil.disk_usage(config.config.DATA_DIR)
    if stat.free >= _EMERGENCY_DISK_MIN_BYTES:
        return

    while store.num_chunks > 1 and stat.free < _EMERGENCY_DISK_MIN_BYTES:
        logger.warning(
            "Emergency disk evict: only {free_mb} MB free on {dir} — dropping oldest LMDB chunk",
            free_mb=round(stat.free / (1024 * 1024)),
            dir=store.directory,
        )
        store.delete_oldest()
        stat = shutil.disk_usage(config.config.DATA_DIR)

