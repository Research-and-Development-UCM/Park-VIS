import asyncio, httpx, json, datetime, os, time, hashlib
import cv2
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from . import models, database, inference
from .stream_manager import stream_manager
from .logging_config import vulture_logger as logger

_state_cache = {}
_stop_event = asyncio.Event()
_trigger_event = asyncio.Event()
_settings_changed_event = asyncio.Event()
_tasks = []
_lock_file_obj = None # Keep reference to prevent GC closing the file

def _resize_image(content: bytes, max_dim: int, quality: int) -> bytes:
    """Decode, downscale (if longest side > max_dim), and re-encode as JPEG."""
    img = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return content
    h, w = img.shape[:2]
    longest = max(w, h)
    if longest <= max_dim:
        return content
    scale = max_dim / longest
    new_w, new_h = int(w * scale), int(h * scale)
    img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def reset_scheduler_events():
    """Re-creates events on the current running loop. Essential for unit tests."""
    global _stop_event, _trigger_event, _settings_changed_event
    _stop_event = asyncio.Event()
    _trigger_event = asyncio.Event()
    _settings_changed_event = asyncio.Event()

def get_latest_state(camera_id: int):
    return _state_cache.get(camera_id, {})

def trigger_inference():
    logger.info("Wakeup triggered (Manual/New Camera)")
    _trigger_event.set()

def notify_settings_changed():
    logger.info("Wakeup triggered (Settings Changed)")
    _settings_changed_event.set()

def stop_background():
    logger.info("Signaling background scheduler tasks to stop...")
    _stop_event.set()
    for task in _tasks:
        task.cancel()

def _hydrate_cache():
    """Populates the in-memory cache with the last known state from the DB.

    Critically, we only hydrate entries for cameras that STILL EXIST
    in the Camera table. Without this filter, the cache accumulates
    phantom ``camera_id`` keys whenever a camera is deleted — its
    historical Occupancy/Scan rows persist with the old ``camera_id``
    forever, and the cache ends up reporting wildly inflated camera
    counts (e.g. a 8-camera device reporting 209).
    """
    logger.info("Hydrating cache from database...")
    db: Session = database.SessionLocal()
    try:
        # Get the set of camera_ids that are real + enabled right now
        valid_cam_ids = {
            c.id
            for c in db.query(models.Camera.id).filter_by(is_enabled=True).all()
        }

        # 1. Hydrate Space States — only for cameras that still exist
        from sqlalchemy import select
        subq = select(func.max(models.Occupancy.id)).group_by(models.Occupancy.space_id)
        latest_occupancies = db.query(models.Occupancy).filter(models.Occupancy.id.in_(subq)).all()

        all_spaces = {s.id: s for s in db.query(models.Space).all()}

        for occ in latest_occupancies:
            space = all_spaces.get(occ.space_id)
            # Skip occupancy records whose camera was deleted
            if space and space.camera_id in valid_cam_ids:
                _state_cache.setdefault(space.camera_id, {})[space.id] = {
                    "occupied": occ.occupied,
                    "name": space.name
                }

        # 2. Hydrate Metadata — only for cameras that still exist
        subq_scans = select(func.max(models.CameraScan.id)).group_by(models.CameraScan.camera_id)
        latest_scans = db.query(models.CameraScan).filter(models.CameraScan.id.in_(subq_scans)).all()

        for scan in latest_scans:
            if scan.camera_id in valid_cam_ids:
                _state_cache.setdefault(scan.camera_id, {})["metadata"] = {
                    "inference_speed": scan.inference_speed,
                    "timestamp": scan.timestamp.isoformat(),
                    "scan_id": scan.id
                }

        logger.info(
            "Hydrated cache with {count} space states across {cams} cameras.",
            count=len(latest_occupancies),
            cams=len(valid_cam_ids),
        )
    except Exception as e:
        logger.error("Error hydrating cache: {e}", e=e)
    finally:
        db.close()


def _purge_stale_cache_entries():
    """Drop any cached state for cameras that no longer exist.

    This is a safety net for installations that were running BEFORE
    the camera-count fix in ``_hydrate_cache`` — they may have
    bloated ``_state_cache`` with phantom entries from deleted
    cameras. Running this on startup cleans those up so the WebSocket
    stops sending stale updates and reports the right
    count immediately (instead of waiting for a restart).
    """
    db = database.SessionLocal()
    try:
        valid_cam_ids = {
            c.id
            for c in db.query(models.Camera.id).filter_by(is_enabled=True).all()
        }
        stale_ids = [cid for cid in _state_cache if cid not in valid_cam_ids]
        if stale_ids:
            for cid in stale_ids:
                _state_cache.pop(cid, None)
            logger.info(
                "Purged {n} stale camera entries from state cache.",
                n=len(stale_ids),
            )
    except Exception as e:
        logger.error("Error purging stale cache: {e}", e=e)
    finally:
        db.close()

async def _maintenance_loop():
    """Performs hourly aggregation and purges old data/blobs based on settings."""
    try:
        while not _stop_event.is_set():
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=3600)
                break 
            except asyncio.TimeoutError:
                pass 
            
            if _stop_event.is_set(): break
            
            db: Session = database.SessionLocal()
            try:
                from .maintenance import run_maintenance
                run_maintenance(db)
            except Exception as e:
                logger.error("Maintenance internal error: {e}", e=e)
            finally:
                db.close()

    except asyncio.CancelledError:
        logger.info("Maintenance loop cancelled.")
    except Exception as e:
        logger.error("Maintenance loop fatal error: {e}", e=e)
    finally:
        logger.info("Maintenance loop exited.")

async def _run_loop():
    # Track the last timestamp we processed for each camera to avoid redundant inference
    last_frame_times = {} 
    last_snapshot_hashes = {} # {camera_id: sha256_hash}
    _prev_camera_ids = set()

    # --- REUSABLE HELPERS (Defined once outside loop) ---
    def _get_params():
        db_settings = database.SessionLocal()
        try:
            rows = db_settings.query(models.Setting).filter(
                models.Setting.key.in_([
                    "inference_interval", "hysteresis_occupied_threshold",
                    "hysteresis_free_threshold", "max_inference_resolution",
                    "quality_snapshots", "quality_crops",
                    "max_snapshot_resolution"
                ])
            ).all()
            s = {r.key: r.value for r in rows}

            interval = int(s.get("inference_interval", 60))
            occ_thresh = float(s.get("hysteresis_occupied_threshold", 0.75))
            free_thresh = float(s.get("hysteresis_free_threshold", 0.25))
            max_res = int(s.get("max_inference_resolution", 1440))
            quality_snap = int(s.get("quality_snapshots", 85))
            quality_crop = int(s.get("quality_crops", 90))
            max_snap_res = int(s.get("max_snapshot_resolution", 1920))
            
            all_cameras = db_settings.query(models.Camera).filter_by(is_enabled=True).all()
            cams_data = []
            for cam in all_cameras:
                cams_data.append({
                    'id': cam.id,
                    'name': cam.name,
                    'source_type': cam.source_type,
                    'is_test': cam.is_test,
                    'local_path': cam.local_path,
                    'snapshot_url': cam.snapshot_url,
                    'stream_url': cam.stream_url,
                    'stream_user': cam.stream_user,
                    'stream_password': cam.stream_password,
                    'stream_res': cam.stream_resolution,
                    'stream_fps': cam.stream_fps,
                    'youtube_mode': cam.youtube_mode
                })
            return interval, occ_thresh, free_thresh, max_res, quality_snap, quality_crop, max_snap_res, cams_data
        finally:
            db_settings.close()

    def _get_spaces(cam_id):
        res_spaces = []
        db = database.SessionLocal()
        try:
            db_spaces = db.query(models.Space).filter_by(camera_id=cam_id).all()
            for sp in db_spaces:
                res_spaces.append({'id': sp.id, 'name': sp.name, 'points': json.loads(sp.points)})
            return res_spaces
        finally:
            db.close()

    def _do_save_results(cam_id, elapsed, content, spaces_data, probs_dict, occ_thresh, free_thresh, max_snap_res):
        db = database.SessionLocal()
        try:
            scan_time = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
            scan = models.CameraScan(camera_id=cam_id, timestamp=scan_time, inference_speed=elapsed)
            db.add(scan)
            db.flush()
            resized = _resize_image(content, max_snap_res, quality_snap)
            database.snapshot_db.put(str(scan.id), resized, timestamp=scan.id)

            changed_spaces = []
            change_details = {}

            for sp_meta in spaces_data:
                sp_id = sp_meta['id']
                prob = probs_dict.get(sp_id, 0.0)
                prev_state = _state_cache.get(cam_id, {}).get(sp_id, {}).get("occupied", False)

                if prob > occ_thresh: occupied = True
                elif prob < free_thresh: occupied = False
                else: occupied = prev_state

                db.add(models.Occupancy(space_id=sp_id, scan_id=scan.id, occupied=occupied))

                if _state_cache.get(cam_id, {}).get(sp_id, {}).get("occupied") is None or prev_state != occupied:
                    changed_spaces.append({'id': sp_id, 'points': sp_meta['points']})
                    change_details[sp_id] = (occupied, prob)

                _state_cache.setdefault(cam_id, {})[sp_id] = {"occupied": occupied, "name": sp_meta['name']}

            _state_cache[cam_id]["metadata"] = {
                "inference_speed": elapsed,
                "timestamp": scan_time.isoformat(),
                "scan_id": scan.id,
                "is_processing": False
            }
            db.commit()
            return changed_spaces, change_details, scan_time, scan.id
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def _do_save_empty_scan(cam_id, content, max_snap_res):
        """Create a CameraScan row for a camera with no parking spaces
        defined, so the dashboard thumbnail still refreshes.

        The dashboard image is cache-busted by ``scan_id`` — the
        frontend listens for ``scan_id`` changes on the WebSocket
        metadata broadcast and reloads the thumbnail via
        ``/api/cameras/{id}/snapshot?v=<scan_id>``. Without a
        ``scan_id`` change, the cached image stays on screen
        indefinitely.

        The full inference + Occupancy + OccupancyEvent pipeline is
        skipped — there's nothing to infer on. We still create the
        ``CameraScan`` row (so the scan_id is queryable) and store
        the JPEG in LMDB (so the snapshot URL returns the fresh
        image). Existing retention purges (``retention_raw_data_days``,
        ``retention_images_hours``) keep DB / LMDB growth bounded.
        """
        db = database.SessionLocal()
        try:
            scan_time = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
            scan = models.CameraScan(
                camera_id=cam_id,
                timestamp=scan_time,
                inference_speed=0.0,  # no inference ran
            )
            db.add(scan)
            db.flush()
            resized = _resize_image(content, max_snap_res, quality_snap)
            database.snapshot_db.put(str(scan.id), resized, timestamp=scan.id)

            _state_cache[cam_id]["metadata"] = {
                "inference_speed": 0.0,
                "timestamp": scan_time.isoformat(),
                "scan_id": scan.id,
                "is_processing": False,
            }
            db.commit()
            return scan.id
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def _do_save_events(change_details, crops, scan_time):
        db = database.SessionLocal()
        try:
            for sp_id, (is_occupied, confidence) in change_details.items():
                event = models.OccupancyEvent(
                    space_id=sp_id,
                    timestamp=scan_time,
                    event_type='occupied' if is_occupied else 'vacated',
                    confidence=confidence
                )
                db.add(event)
                db.flush() 
                crop_data = crops.get(sp_id)
                if crop_data: database.crop_db.put(str(event.id), crop_data, timestamp=event.id)
            db.commit()
        finally:
            db.close()

    last_run_start_time = time.perf_counter() - 1000000.0
    try:
        while not _stop_event.is_set():
            was_triggered = _trigger_event.is_set()
            _trigger_event.clear()
            _settings_changed_event.clear()
            
            try:
                # 1 & 2. Get Params and Cameras
                interval, occ_thresh, free_thresh, max_res, quality_snap, quality_crop, max_snap_res, cams_data = await asyncio.to_thread(_get_params)

                current_time = time.perf_counter()
                elapsed_since_last_run = current_time - last_run_start_time
                should_run = (elapsed_since_last_run >= interval) or was_triggered

                if should_run:
                    last_run_start_time = time.perf_counter()

                    # Clean up state for cameras that were removed or disabled
                    current_ids = {cam['id'] for cam in cams_data}
                    stale_ids = _prev_camera_ids - current_ids
                    for sid in stale_ids:
                        last_frame_times.pop(sid, None)
                        last_snapshot_hashes.pop(sid, None)
                        _state_cache.pop(sid, None)
                    _prev_camera_ids = current_ids

                    # 3. Process each camera
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        for cam in cams_data:
                            if _stop_event.is_set(): break
                            cam_id, cam_name = cam['id'], cam['name']
                            
                            # --- PHASE 1: Image Acquisition (No DB lock) ---
                            content = None
                            try:
                                if cam['source_type'] == "test" or (cam['is_test'] and cam['local_path']):
                                    if not cam['local_path'] or not os.path.exists(cam['local_path']):
                                        logger.warning("Test camera {cid} missing file: {path}", cid=cam_id, path=cam['local_path'])
                                        continue
                                    with open(cam['local_path'], "rb") as f:
                                        content = f.read()
                                elif cam['source_type'] in ["rtsp", "youtube", "video"]:
                                    # Was this stream already active?
                                    stream_was_active = False
                                    with stream_manager._lock:
                                        if cam_id in stream_manager.streams and stream_manager.streams[cam_id]['active']:
                                            stream_was_active = True

                                    stream_manager.start_stream(
                                        cam_id, 
                                        cam['source_type'], 
                                        cam['stream_url'], 
                                        cam['stream_user'], 
                                        cam['stream_password'], 
                                        res=cam['stream_res'], 
                                        fps=cam['stream_fps'],
                                        youtube_mode=cam.get('youtube_mode')
                                    )
                                    
                                    # If the stream was just started, yield for 2.0s to let the GStreamer pipeline initialize
                                    # and receive its first frame, preventing a 60-second delay on first inference.
                                    if not stream_was_active:
                                        await asyncio.sleep(2.0)
                                    
                                    last_ts = last_frame_times.get(cam_id, 0)
                                    now_ts = time.time()
                                    force_refresh = (now_ts - last_ts > 1800)
                                    
                                    frame = stream_manager.get_latest_frame(cam_id, since=(None if force_refresh else last_ts))
                                    
                                    if frame is not None:
                                        with stream_manager._lock:
                                            if cam_id in stream_manager.streams:
                                                last_frame_times[cam_id] = stream_manager.streams[cam_id]['last_frame_time']
                                            else:
                                                last_frame_times[cam_id] = time.time()

                                        # Offload JPEG encoding to background thread
                                        _, buffer = await asyncio.to_thread(cv2.imencode, '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality_snap])
                                        content = buffer.tobytes()
                                    else:
                                        continue
                                else:
                                    if not cam['snapshot_url']: continue
                                    try:
                                        resp = await client.get(cam['snapshot_url'])
                                        resp.raise_for_status()
                                        new_content = resp.content
                                        
                                        # Offload hash calculation to background thread
                                        new_hash = await asyncio.to_thread(lambda: hashlib.sha256(new_content).hexdigest())
                                        last_ts = last_frame_times.get(cam_id, 0)
                                        now_ts = time.time()
                                        force_refresh = (now_ts - last_ts > 1800)
                                        
                                        if not force_refresh and new_hash == last_snapshot_hashes.get(cam_id):
                                            continue
                                        
                                        content = new_content
                                        last_snapshot_hashes[cam_id] = new_hash
                                        last_frame_times[cam_id] = now_ts
                                    except Exception as e:
                                        logger.error("Failed to fetch snapshot for camera {cid}: {e}", cid=cam_id, e=e)
                                        continue
                            except Exception as e:
                                logger.error("Acquisition error for camera {cid}: {e}", cid=cam_id, e=e)
                                continue

                            if not content: continue

                            # Update metadata: flip is_processing to True for real-time UI status
                            existing_meta = _state_cache.get(cam_id, {}).get("metadata", {})
                            _state_cache.setdefault(cam_id, {})["metadata"] = {
                                "inference_speed": existing_meta.get("inference_speed", 0),
                                "timestamp": existing_meta.get("timestamp", datetime.datetime.now(datetime.UTC).isoformat()),
                                "scan_id": existing_meta.get("scan_id"),
                                "is_processing": True
                            }

                            # --- PHASE 2: Fetch Metadata ---
                            spaces_data = await asyncio.to_thread(_get_spaces, cam_id)

                            if not spaces_data:
                                # No parking spaces defined for this camera.
                                # Skip inference (nothing to infer on) but
                                # still create a CameraScan row + LMDB blob
                                # so the dashboard thumbnail refreshes via
                                # the WebSocket scan_id broadcast. See
                                # _do_save_empty_scan for the rationale.
                                try:
                                    await asyncio.to_thread(_do_save_empty_scan, cam_id, content, max_snap_res)
                                except Exception as e:
                                    logger.error("Failed empty-scan save for camera {cid}: {e}", cid=cam_id, e=e)
                                continue

                            # --- PHASE 3: Inference ---
                            if _stop_event.is_set(): break
                            logger.debug("Starting inference for camera: {name} (ID: {cid})", name=cam_name, cid=cam_id)
                            probs_dict, elapsed, actual_res = await inference.run_inference(cam_id, content, spaces_data, max_res=max_res)
                            if _stop_event.is_set(): break
                            logger.debug("Finished inference for camera: {name} ({w}x{h}) in {ms:.0f}ms", name=cam_name, w=actual_res[0], h=actual_res[1], ms=elapsed*1000)

                            # --- PHASE 4: Save Results ---
                            changed_spaces, change_details, scan_time, scan_id = await asyncio.to_thread(
                                _do_save_results, cam_id, elapsed, content, spaces_data, probs_dict, occ_thresh, free_thresh, max_snap_res
                            )

                            if changed_spaces and not _stop_event.is_set():
                                crops = await inference.get_crops(cam_id, content, changed_spaces, max_res=max_res, quality=quality_crop)
                                await asyncio.to_thread(_do_save_events, change_details, crops, scan_time)

            except Exception as e:
                logger.error("Scheduler loop internal error: {e}", e=e)
            
            elapsed = time.perf_counter() - last_run_start_time
            sleep_time = max(0.0, interval - elapsed)
            
            try:
                stop_wait = asyncio.create_task(_stop_event.wait())
                trigger_wait = asyncio.create_task(_trigger_event.wait())
                settings_wait = asyncio.create_task(_settings_changed_event.wait())
                done, pending = await asyncio.wait(
                    [stop_wait, trigger_wait, settings_wait],
                    timeout=sleep_time,
                    return_when=asyncio.FIRST_COMPLETED
                )
                for p in pending: p.cancel()
                if _stop_event.is_set(): break
            except Exception as e:
                logger.error("Scheduler wait error: {e}", e=e)
    except asyncio.CancelledError:
        logger.info("Scheduler loop cancelled.")
    finally:
        logger.info("Scheduler loop exited.")

async def _optimization_loop():
    """Performs deep maintenance (SQLite VACUUM, LMDB Compact) on a weekly basis."""
    try:
        while not _stop_event.is_set():
            db = database.SessionLocal()
            try:
                s_opt = db.query(models.Setting).filter_by(key="optimization_interval_days").first()
                interval_days = int(s_opt.value) if s_opt else 7
            finally:
                db.close()

            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=interval_days * 86400)
                break 
            except asyncio.TimeoutError:
                pass 

            if _stop_event.is_set(): break

            logger.info("Starting weekly system optimization...")
            db = database.SessionLocal()
            try:
                logger.info("Running SQLite VACUUM...")
                db.execute(text("VACUUM"))
                db.commit()
            except Exception as e:
                logger.error("SQLite VACUUM error: {e}", e=e)
            finally:
                db.close()

            logger.info("System optimization complete.")
    except asyncio.CancelledError:
        logger.info("Optimization loop cancelled.")
    finally:
        logger.info("Optimization loop exited.")

def start_background():
    global _tasks, _lock_file_obj
    if not _tasks:
        logger.info("Starting background scheduler (Process ID: {pid})", pid=os.getpid())
        _hydrate_cache()
        _purge_stale_cache_entries()
        _tasks.append(asyncio.create_task(_run_loop()))
        _tasks.append(asyncio.create_task(_maintenance_loop()))
        _tasks.append(asyncio.create_task(_optimization_loop()))

