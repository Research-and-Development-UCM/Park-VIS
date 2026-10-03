import os
import sys
from sqlalchemy import text
from .. import database, models, maintenance
from ..logging_config import vulture_logger as logger
from ..config import config

def compact():
    logger.info("=== Park VIS Database Optimizer ===")
    
    # 1. SQLite Optimization
    logger.info("Optimizing SQLite (parkinglot.db)...")
    db_path = config.DB_PATH
    db = database.SessionLocal()
    try:
        start_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        logger.info("Starting size: {size:.2f} MB", size=start_size / (1024*1024))
        
        # We use raw connection to ensure no transaction is active
        conn = database.engine.raw_connection()
        cursor = conn.cursor()
        logger.info("Running VACUUM (this may take a minute)...")
        cursor.execute("VACUUM")
        conn.close()
        
        end_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        logger.info("Final size: {size:.2f} MB", size=end_size / (1024*1024))
        logger.info("Space reclaimed: {size:.2f} MB", size=(start_size - end_size) / (1024*1024))
    except Exception as e:
        logger.error("SQLite error: {e}", e=e)
    finally:
        db.close()

    logger.info("Optimization process finished.")

def prune():
    logger.info("=== Park VIS Data Pruning ===")
    db = database.SessionLocal()
    try:
        maintenance.run_maintenance(db)
        logger.info("Pruning complete.")
    except Exception as e:
        logger.error("Error during pruning: {e}", e=e)
    finally:
        db.close()
