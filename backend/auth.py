import jwt, datetime, json, secrets, hashlib
from fastapi import HTTPException, status, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from passlib.hash import bcrypt as _bcrypt
from sqlalchemy.orm import Session
from . import models, database, schemas
from .logging_config import vulture_logger as logger
from .config import config
from opentelemetry import trace

SECRET = config.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 24 hours

ADMIN_PERMISSIONS = ["manage_cameras", "view_history", "view_metrics", "edit_settings", "manage_users", "manage_api_keys", "view_diagnostics", "manage_billing", "manage_alerts"]


def parse_permissions(user: models.User) -> list:
    """Return ``user.permissions`` as a list, defensively.

    The column is stored as a JSON string but can be NULL (legacy rows
    before the column was populated, or manual SQL edits) or corrupt.
    All 9 call sites that previously did ``json.loads(user.permissions)``
    used to surface a 500 to the user in that case. Now they get an
    empty list, which is the same response a brand-new non-admin user
    with no explicit permissions would get.
    """
    raw = getattr(user, "permissions", None)
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (TypeError, ValueError):
        logger.warning("user {uid} has corrupt permissions JSON; treating as []", uid=getattr(user, "id", "?"))
        return []


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt.

    Used by ``login_user`` (to re-hash legacy plaintext rows on the
    way in) and by the user-management CRUD (to write new accounts).
    Bcrypt's 72-byte input cap is handled by ``passlib``'s
    internal truncation, so callers don't need to pre-truncate.
    """
    return _bcrypt.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt verification."""
    try:
        return _bcrypt.verify(plain, hashed)
    except (ValueError, TypeError):
        # Malformed hash → treat as failed verification, don't raise.
        return False


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def generate_api_key():
    # Generate 32 char random key
    key = secrets.token_urlsafe(24) 
    return key

def get_auth_user(request: Request, db: Session = Depends(database.get_db), token: str = Depends(oauth2_scheme)):
    """Unified authentication: Checks JWT first, then X-API-Key header."""
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("auth.get_auth_user"):
        auth_header = request.headers.get("Authorization")
    logger.debug("Request path: {path} | Header present: {h} | Token present: {t}", path=request.url.path, h=bool(auth_header), t=bool(token))

    # 1. Check JWT Token from header, query parameter, or cookie
    jwt_token = token or request.query_params.get("token") or request.cookies.get("token")
    if jwt_token:
        try:
            payload = jwt.decode(jwt_token, SECRET, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                user = db.query(models.User).filter_by(username=username).first()
                if user:
                    return user
                else:
                    logger.warning("User '{user}' not found in database.", user=username)
            else:
                logger.warning("Token missing 'sub' claim.")
        except jwt.ExpiredSignatureError:
            logger.info("Token expired.")
        except jwt.PyJWTError as e:
            logger.error("JWT Decode Error: {e}", e=e)
    elif auth_header:
         logger.debug("Token missing from dependency but Authorization header found: {h}...", h=auth_header[:25])

    
    # 2. Check X-API-Key Header
    api_key = request.headers.get("X-API-Key")
    if api_key:
        try:
            h = hash_key(api_key)
            db_key = db.query(models.APIKey).filter_by(hashed_key=h).first()
            if db_key:
                # Log activity using background session to avoid blocking
                isolated_db = database.SessionLocal()
                try:
                    isolated_db.query(models.APIKey).filter_by(id=db_key.id).update(
                        {"last_used_at": datetime.datetime.now(datetime.UTC)}
                    )
                    isolated_db.commit()
                finally:
                    isolated_db.close()
                return db_key.creator
            else:
                logger.warning("Invalid API Key used.")
        except Exception as e:
            logger.error("API Key validation error: {e}", e=e)
    
    return None

def get_user_perms(user: models.User = Depends(get_auth_user)):
    if not user:
        return []
    if user.is_admin:
        # Admins get everything
        return ADMIN_PERMISSIONS
    return parse_permissions(user)

def check_permission_direct(user: models.User, required: str):
    if not user: return False
    if user.is_admin: return True
    return required in parse_permissions(user)

def login_user(form: schemas.LoginForm, db: Session):
    """Authenticate a user and return a JWT.

    Passwords are bcrypt-hashed from the start.  We only verify
    against ``user.password_hash`` — the legacy plaintext column
    was removed (the app was never deployed with it).
    """
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("auth.login_user"):
        user = db.query(models.User).filter_by(username=form.username).first()
        if not user:
            return None

        if not user.password_hash:
            # No password set (e.g. fresh install, no admin yet).
            return None

        if not verify_password(form.password, user.password_hash):
            return None

        token = create_access_token({
            "sub": user.username,
            "is_admin": user.is_admin,
            "permissions": parse_permissions(user),
        })
        return {
            "access_token": token,
            "token_type": "bearer",
            "is_admin": user.is_admin,
            "permissions": parse_permissions(user),
        }

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET, algorithm=ALGORITHM)
    return encoded_jwt
