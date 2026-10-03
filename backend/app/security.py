import hmac
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .utils import now

_bearer = HTTPBearer(auto_error=False)


def check_credentials(email: str, password: str) -> bool:
    return (hmac.compare_digest(email.strip().lower(), settings.ADMIN_EMAIL.lower())
            and hmac.compare_digest(password, settings.ADMIN_PASSWORD))


def create_token(email: str) -> str:
    payload = {"sub": email, "role": "hr", "exp": now() + timedelta(hours=settings.JWT_EXPIRE_HOURS)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def require_hr(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")
    try:
        payload = jwt.decode(creds.credentials, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please log in again")
    if payload.get("role") != "hr":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
    return payload["sub"]
