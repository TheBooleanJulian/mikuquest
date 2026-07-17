import os
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SESSION_COOKIE   = "mq_session"
SESSION_MAX_AGE  = 30 * 24 * 3600  # 30 days
_SALT            = "miguquest-web-session"


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError("SESSION_SECRET environment variable is not set.")
    return URLSafeTimedSerializer(secret, salt=_SALT)


def create_session_value(chat_id: int) -> str:
    return _serializer().dumps({"chat_id": chat_id})


def read_session_chat_id(cookie_value: Optional[str]) -> Optional[int]:
    if not cookie_value:
        return None
    try:
        data = _serializer().loads(cookie_value, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("chat_id")
