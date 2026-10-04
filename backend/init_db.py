from sqlalchemy import inspect, text
from . import database, models
from .logging_config import vulture_logger as logger

def main():
    database.init_db()
    
    db = database.SessionLocal()
    inspector = inspect(database.engine)
    
    try:
        # Simple migrations for existing databases
        tables = inspector.get_table_names()
        
        if 'camera_scans' in tables:
            columns = [c['name'] for c in inspector.get_columns('camera_scans')]
            if 'inference_speed' not in columns:
                logger.info("Migration: Adding inference_speed column to camera_scans")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE camera_scans ADD COLUMN inference_speed FLOAT"))
                    conn.commit()
            if 'has_image' not in columns:
                logger.info("Migration: Adding has_image column to camera_scans")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE camera_scans ADD COLUMN has_image BOOLEAN DEFAULT 1"))
                    conn.commit()

        if 'occupancy_hourly' in tables:
            columns_hourly = [c['name'] for c in inspector.get_columns('occupancy_hourly')]
            if 'avg_inference_speed' not in columns_hourly:
                logger.info("Migration: Adding avg_inference_speed column to occupancy_hourly")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE occupancy_hourly ADD COLUMN avg_inference_speed FLOAT"))
                    conn.commit()

        if 'occupancy_events' in tables:
            columns_events = [c['name'] for c in inspector.get_columns('occupancy_events')]
            if 'confidence' not in columns_events:
                logger.info("Migration: Adding confidence column to occupancy_events")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE occupancy_events ADD COLUMN confidence FLOAT"))
                    conn.commit()
            if 'has_crop' not in columns_events:
                logger.info("Migration: Adding has_crop column to occupancy_events")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE occupancy_events ADD COLUMN has_crop BOOLEAN DEFAULT 1"))
                    conn.commit()

        if 'alert_events' in tables:
            columns_ae = [c['name'] for c in inspector.get_columns('alert_events')]
            if 'next_retry_at' not in columns_ae:
                logger.info("Migration: Adding next_retry_at column to alert_events")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE alert_events ADD COLUMN next_retry_at DATETIME"))
                    conn.commit()

        if 'alert_state_per_space' in tables:
            # The hysteresis refactor (space_edge dynamic firing target)
            # added this column to the model, but ``create_all`` only
            # creates new tables — it does NOT add new columns to
            # existing ones.  Without this migration the alert engine
            # tick raises ``no such column: alert_state_per_space.firing_target``
            # every cycle and silently swallows the failure.
            columns_asp = [c['name'] for c in inspector.get_columns('alert_state_per_space')]
            if 'firing_target' not in columns_asp:
                logger.info("Migration: Adding firing_target column to alert_state_per_space")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE alert_state_per_space ADD COLUMN firing_target BOOLEAN"))
                    conn.commit()

        if 'users' in tables:
            columns_users = [c['name'] for c in inspector.get_columns('users')]
            if 'is_admin' not in columns_users:
                logger.info("Migration: Adding is_admin column to users")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
                    conn.commit()
            if 'password_hash' not in columns_users:
                logger.info("Migration: Adding password_hash column to users")
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR"))
                    conn.commit()

        # C3 audit fix: DO NOT create a default admin/admin user.
        # Fresh installs must call POST /api/setup/admin to set up
        # the first administrator.  A loud startup log line tells
        # the operator what to do.

        if 'cameras' in tables:
            columns_cameras = [c['name'] for c in inspector.get_columns('cameras')]
            for col, ddl in [
                ('source_type', "VARCHAR DEFAULT 'snapshot'"),
                ('stream_url', "VARCHAR"),
                ('stream_user', "VARCHAR"),
                ('stream_password', "VARCHAR"),
                ('stream_resolution', "VARCHAR DEFAULT '1080'"),
                ('stream_fps', "INTEGER DEFAULT 5"),
                ('youtube_mode', "VARCHAR DEFAULT 'stream'"),
                ('is_enabled', "BOOLEAN DEFAULT 1"),
                ('is_test', "BOOLEAN DEFAULT 0"),
                ('local_path', "VARCHAR")
            ]:
                if col not in columns_cameras:
                    logger.info("Migration: Adding {col} column to cameras", col=col)
                    with database.engine.connect() as conn:
                        conn.execute(text(f"ALTER TABLE cameras ADD COLUMN {col} {ddl}"))
                        conn.commit()

        # C2 audit fix removed: the app was never deployed with
        # legacy plaintext passwords, so we don't need to mark users
        # for forced reset or migrate on first login.  Every user
        # now authenticates with bcrypt from the start (see
        # ``backend.auth.login_user``).

        # C3 audit fix: the previous code unconditionally created
        # an `admin/admin` user here.  That is now removed; the
        # first administrator is created via POST /api/setup/admin.
        # A fresh install's UI redirects to /setup on first load
        # to walk the operator through this step.
        existing_admin = (
            db.query(models.User).filter(models.User.is_admin == True).first()
        )
        if existing_admin is None:
            logger.warning(
                "No administrator user exists. The first login attempt "
                "will be redirected to /setup where the operator creates "
                "the initial admin account."
            )
        
        # Default settings
        defaults = {
            "inference_interval": "60",
            "confidence_threshold": "0.6",
            "retention_images_hours": "24",
            "retention_events_days": "30",
            "retention_raw_data_days": "7",
            "retention_hourly_data_days": "365",
            "inference_device": "cpu",
            "inference_backend": "vulturevision",
            "max_inference_resolution": "1440",
            "hysteresis_occupied_threshold": "0.75",
            "hysteresis_free_threshold": "0.25",
            "optimization_interval_days": "7",
            "max_snapshot_resolution": "1920",
            "quality_snapshots": "85",
            "quality_crops": "90",
            # Alerting subsystem (system-wide; users supply their own SMTP)
            "alerting_enabled": "false",
            "alerting_smtp_host": "",
            "alerting_smtp_port": "587",
            "alerting_smtp_user": "",
            "alerting_smtp_pass": "",
            "alerting_smtp_from": "",
            "alerting_smtp_use_tls": "true",
            # EULA acceptance tracking
            "eula_accepted": "false",
            "eula_accepted_at": "",
            "eula_accepted_by": "",
            "eula_accepted_ip": "",
            # SSL / HTTPS settings
            "ssl_enabled": "false",
            "ssl_cert_path": "",
            "ssl_key_path": "",
        }
        
        for key, val in defaults.items():
            s = db.query(models.Setting).filter_by(key=key).first()
            if not s:
                logger.info("Setting default: {key} = {val}", key=key, val=val)
                db.add(models.Setting(key=key, value=val))
            elif key == "inference_device" and s.value == "auto":
                logger.info("Migration: Switching inference_device from 'auto' to 'cpu'")
                s.value = "cpu"
        
        db.commit()

        # create default camera
        cam = db.query(models.Camera).first()
        if not cam:
            logger.info("Creating demo camera...")
            import os
            import shutil
            import json

            backend_dir = os.path.dirname(os.path.abspath(__file__))
            root_dir = os.path.dirname(backend_dir)

            # Copy demo video to uploads/cameras/ if it exists in demovideo/
            src_video = os.path.join(root_dir, "demovideo", "initial_parking_lot_demo.mp4")
            dest_dir = "uploads/cameras"
            dest_video = os.path.join(dest_dir, "initial_parking_lot_demo.mp4")

            if os.path.exists(src_video):
                os.makedirs(dest_dir, exist_ok=True)
                if not os.path.exists(dest_video):
                    logger.info("Copying demo video {src} to {dest}", src=src_video, dest=dest_video)
                    shutil.copy(src_video, dest_video)
            else:
                logger.warning("Demo video source file not found at {src}", src=src_video)

            # Create default camera as local video
            cam = models.Camera(
                name="Park VIS Demo Camera",
                source_type="video",
                stream_url="uploads/cameras/initial_parking_lot_demo.mp4",
                is_enabled=True
            )
            db.add(cam)
            db.commit()
            db.refresh(cam)

            # Prepopulate spaces from demovideo/initial_parking_lot_demo_spaces.json
            default_spaces_path = os.path.join(root_dir, "demovideo", "initial_parking_lot_demo_spaces.json")
            if os.path.exists(default_spaces_path):
                logger.info("Loading default spaces from {path}", path=default_spaces_path)
                with open(default_spaces_path, "r") as f:
                    spaces_data = json.load(f)

                for s in spaces_data:
                    space = models.Space(
                        camera_id=cam.id,
                        name=s["name"],
                        points=json.dumps(s["points"])
                    )
                    db.add(space)
                db.commit()
                logger.info("Prepopulated {count} spaces for the demo camera.", count=len(spaces_data))
            else:
                logger.warning("Default spaces file not found at {path}", path=default_spaces_path)

        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error("Database initialization failed: {e}", e=e)
    finally:
        db.close()

if __name__ == "__main__":
    main()
