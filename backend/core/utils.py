"""Tiny shared helpers (time + id generation)."""
import uuid
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def _layout_for_bus_type(bus_type: str) -> str:
    """Map a bus class name to its persisted seat-column layout."""
    bt = (bus_type or "").strip().lower()
    if "vip" in bt:
        return "2+1"
    return "2+2"
