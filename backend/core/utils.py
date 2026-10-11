"""Tiny shared helpers (time + id generation)."""
import uuid
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())
