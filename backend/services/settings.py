"""Shared application settings lookup."""
from core.config import db
from core.utils import utcnow

DEFAULT_SGD_TO_MYR = 3.50

async def _get_settings() -> dict:
    doc = await db.app_settings.find_one({"id": "global"}, {"_id": 0})
    if not doc:
        doc = {
            "id": "global",
            "sgd_to_myr_rate": DEFAULT_SGD_TO_MYR,
            "updated_at": utcnow().isoformat(),
        }
        await db.app_settings.insert_one(doc)
    return doc
