import os
from rollinglmdb import RollingLMDB
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .models import Base
from .config import config

# --- RollingLMDB Configuration ---
os.makedirs(config.DATA_DIR, exist_ok=True)

snapshot_db = RollingLMDB(
    config.SNAPSHOT_DB_PATH,
    max_size_gb=float(config.SNAPSHOT_MAX_SIZE_GB),
)
crop_db = RollingLMDB(
    config.CROP_DB_PATH,
    max_size_gb=float(config.CROP_MAX_SIZE_GB),
)

# --- SQLite Configuration ---
SQLALCHEMY_DATABASE_URL = f"sqlite:///{config.DB_PATH}"
# Increase timeout significantly for handled concurrent writes
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False, "timeout": 60},
    echo=config.SQL_ECHO
)

# Enable WAL mode and busy_timeout for better concurrency
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if config.SQL_WAL_MODE:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=60000") # 60 seconds
        cursor.close()
    # SQLite disables foreign-key enforcement by default.  The
    # models declare ``ondelete="CASCADE"`` on several composite
    # keys (e.g. ``camera_group_memberships.camera_group_id`` ->
    # ``camera_groups.id``), but without this pragma SQLite
    # silently ignores them — leading to orphaned rows and
    # composite-PK collisions when a deleted ID is reused.
    # Enabling it per-connection (it doesn't persist across
    # connections) is the standard SQLAlchemy recipe.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
