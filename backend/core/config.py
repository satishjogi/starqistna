"""Shared environment configuration and MongoDB access.

Importing this module requires the same env vars the monolith always required
(MONGO_URL, DB_NAME, JWT_SECRET, STRIPE_API_KEY, BOOTSTRAP_ADMIN_PASSWORD).
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "168"))
STRIPE_API_KEY = os.environ["STRIPE_API_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
if not BOOTSTRAP_ADMIN_PASSWORD:
    raise RuntimeError(
        "BOOTSTRAP_ADMIN_PASSWORD env var is required. Set a strong password in backend/.env"
    )

logger = logging.getLogger("transit")


