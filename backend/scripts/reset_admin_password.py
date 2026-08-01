"""Reset the bootstrap super-admin password on production/staging.

Run this on the VPS whenever the admin gets locked out (typically after a
BOOTSTRAP_ADMIN_PASSWORD change in .env that never got applied to Mongo).

Usage
-----
From the backend directory, with the venv activated::

    python scripts/reset_admin_password.py
      # → prompts for a new password (hidden input), reads MONGO_URL from .env

    python scripts/reset_admin_password.py --password 'MyNewStrongPass!23'
      # → non-interactive; useful for scripted deploys.

    python scripts/reset_admin_password.py --from-env
      # → uses the current BOOTSTRAP_ADMIN_PASSWORD env value.

The script:
  * connects to Mongo using MONGO_URL + DB_NAME from backend/.env
  * finds the admin account (email: admin@starqistna.com)
  * updates the bcrypt password hash
  * clears `must_change_password` so login is immediately usable
  * ensures `is_admin=True`, `role=super_admin`, `is_active=True`
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

import bcrypt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


ADMIN_EMAIL = "admin@starqistna.com"


def _load_env() -> None:
    """Load backend/.env regardless of CWD."""
    here = Path(__file__).resolve().parent.parent  # backend/
    load_dotenv(here / ".env")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset the Star Qistna super-admin password.")
    parser.add_argument("--password", help="New password (non-interactive).")
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="Use the current BOOTSTRAP_ADMIN_PASSWORD env value.",
    )
    parser.add_argument(
        "--email",
        default=ADMIN_EMAIL,
        help=f"Admin email to reset (default: {ADMIN_EMAIL}).",
    )
    return parser.parse_args()


def _pick_password(args: argparse.Namespace) -> str:
    if args.password:
        return args.password
    if args.from_env:
        pw = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        if not pw:
            sys.exit("BOOTSTRAP_ADMIN_PASSWORD is not set in .env")
        return pw
    pw1 = getpass.getpass("New admin password: ")
    pw2 = getpass.getpass("Confirm password:    ")
    if pw1 != pw2:
        sys.exit("Passwords do not match.")
    if len(pw1) < 8:
        sys.exit("Password must be at least 8 characters.")
    return pw1


async def _reset(email: str, new_password: str) -> None:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        sys.exit("MONGO_URL and DB_NAME must be set in backend/.env")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    result = await db.users.update_one(
        {"email": email},
        {
            "$set": {
                "password_hash": hashed,
                "is_admin": True,
                "role": "super_admin",
                "is_active": True,
            },
            "$unset": {"must_change_password": ""},
        },
    )

    if result.matched_count == 0:
        sys.exit(
            f"No user found with email {email!r}. "
            "Start the backend once first so the bootstrap seed runs, then retry."
        )

    print(f"OK. Password reset for {email}. Login is now active.")


def main() -> None:
    _load_env()
    args = _parse_args()
    password = _pick_password(args)
    asyncio.run(_reset(args.email, password))


if __name__ == "__main__":
    main()
