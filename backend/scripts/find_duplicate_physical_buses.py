#!/usr/bin/env python3
"""Find (and optionally fix) duplicate physical-bus schedules.

The refactor introduced in 2026-08-15 says: one route + one date + one departure
time = ONE physical bus = ONE schedule row. Legacy data may have multiple rows
sharing that key (one per pickup/dropoff pair). This script identifies them.

Usage
-----
    # List duplicates (safe, read-only):
    python scripts/find_duplicate_physical_buses.py

    # Delete every duplicate except the earliest per group (destructive):
    python scripts/find_duplicate_physical_buses.py --fix

The kept row is the OLDEST `created_at` in each duplicate group (assumed original).
Any bookings/seat_locks attached to deleted schedules stay in the DB — we log
their references so the operator can manually re-issue tickets if needed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Load backend/.env (script runs from any cwd, even outside the venv).
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main(fix: bool) -> int:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("MONGO_URL and DB_NAME must be set (check backend/.env).", file=sys.stderr)
        return 2
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    pipeline = [
        {"$match": {"route_id": {"$exists": True, "$ne": None}}},
        {"$group": {
            "_id": {"r": "$route_id", "d": "$departure_date", "t": "$departure_time"},
            "n": {"$sum": 1},
            "docs": {"$push": {"id": "$id", "from": "$from_terminal_id",
                               "to": "$to_terminal_id", "created": "$created_at"}},
        }},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1}},
    ]
    groups = [g async for g in db.schedules.aggregate(pipeline)]
    print(f"Found {len(groups)} duplicate physical-bus group(s).\n")
    if not groups:
        return 0

    total_extra = 0
    for g in groups:
        key = g["_id"]
        print(f"  route={key['r']}  date={key['d']}  time={key['t']}  → {g['n']} rows")
        # Sort by created_at ascending — earliest first is the keeper.
        docs = sorted(g["docs"], key=lambda d: d.get("created") or "")
        keeper = docs[0]
        extras = docs[1:]
        total_extra += len(extras)
        print(f"     KEEP    {keeper['id']}  (from={keeper['from']} → to={keeper['to']})  created={keeper.get('created')}")
        for e in extras:
            print(f"     EXTRA   {e['id']}  (from={e['from']} → to={e['to']})  created={e.get('created')}")
        if fix:
            extra_ids = [e["id"] for e in extras]
            # Warn about any bookings/locks that will be orphaned.
            b = await db.bookings.count_documents({"schedule_id": {"$in": extra_ids}})
            lk = await db.seat_locks.count_documents({"schedule_id": {"$in": extra_ids}})
            if b or lk:
                print(f"     ⚠️  {b} booking(s), {lk} seat lock(s) will be orphaned — please re-issue tickets manually.")
            res = await db.schedules.delete_many({"id": {"$in": extra_ids}})
            print(f"     ✂️  deleted {res.deleted_count} extra row(s).")

    print()
    if fix:
        print(f"Done. Deleted {total_extra} extra schedule row(s).")
        print("Restart the backend so the uniq_route_departure index can now build:")
        print("  sudo systemctl restart starqistna-backend")
    else:
        print(f"Read-only run. Re-run with --fix to remove {total_extra} extra row(s) and unblock the unique index.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Find or fix duplicate physical-bus schedules.")
    ap.add_argument("--fix", action="store_true", help="Delete duplicate rows (keep oldest per group).")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.fix)))
