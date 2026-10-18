#!/usr/bin/env python3
"""LEGACY pre-migration checker for whole-bus seat-lock duplicates.

Do not use this tool once the per-leg seat_leg_locks collection exists: seat
reuse on disjoint route segments is valid in the new schema. Use
check_segment_seat_migration.py for the new read-only preflight.

Find (and optionally resolve) duplicate seat_locks that block the
uniq_bus_instance_seat_active index build.

Symptom: after upgrading, backend logs the "Legacy duplicate seat_locks
detected" warning. This happens when two locks share the same
`(bus_instance_id, seat_number)` while both are in status `locked` or
`booked` — historically because sibling schedules for the same physical
bus each had their own independent lock rows.

Usage
-----
    # Inspect duplicates (read-only):
    python scripts/find_duplicate_seat_locks.py

    # Release stale/expired non-booked duplicates (safe):
    python scripts/find_duplicate_seat_locks.py --release-expired

    # Hard cleanup: for every duplicate group, keep the OLDEST 'booked' lock
    # and mark the rest as 'released' (does NOT touch bookings — orphaned
    # bookings must be reconciled manually):
    python scripts/find_duplicate_seat_locks.py --fix
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main(release_expired: bool, fix: bool) -> int:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("MONGO_URL and DB_NAME must be set (check backend/.env).", file=sys.stderr)
        return 2
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # This script groups only by whole bus + seat and would misclassify valid
    # non-overlapping segment reuse after the schema change. Refuse all modes,
    # including --fix/--release-expired, once the new collection exists.
    if "seat_leg_locks" in await db.list_collection_names():
        print(
            "Refusing to run the legacy whole-bus seat-lock tool: seat_leg_locks exists. "
            "Use scripts/check_segment_seat_migration.py (read-only) instead.",
            file=sys.stderr,
        )
        client.close()
        return 2

    if release_expired:
        now_iso = datetime.now(timezone.utc).isoformat()
        result = await db.seat_locks.update_many(
            {"status": "locked", "expires_at": {"$lt": now_iso}},
            {"$set": {"status": "released"}},
        )
        print(f"Released {result.modified_count} expired locked seats.")

    pipeline = [
        {"$match": {"status": {"$in": ["locked", "booked"]},
                    "bus_instance_id": {"$exists": True}}},
        {"$group": {
            "_id": {"b": "$bus_instance_id", "s": "$seat_number"},
            "n": {"$sum": 1},
            "docs": {"$push": {"id": "$id", "schedule_id": "$schedule_id",
                               "status": "$status", "booking_id": "$booking_id",
                               "created": "$created_at"}},
        }},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1}},
    ]
    groups = [g async for g in db.seat_locks.aggregate(pipeline)]
    print(f"\nFound {len(groups)} duplicate seat-lock group(s).")
    if not groups:
        return 0

    fixed = 0
    for g in groups:
        key = g["_id"]
        print(f"\n  bus_instance={key['b']}  seat={key['s']}  → {g['n']} lock(s)")
        # Prefer to keep booked over locked. Within same status, keep oldest.
        docs = sorted(g["docs"],
                      key=lambda d: (0 if d.get("status") == "booked" else 1,
                                     d.get("created") or ""))
        keeper = docs[0]
        extras = docs[1:]
        print(f"     KEEP   status={keeper['status']}  schedule_id={keeper['schedule_id']}  booking_id={keeper.get('booking_id')}  id={keeper['id']}")
        for e in extras:
            print(f"     EXTRA  status={e['status']}  schedule_id={e['schedule_id']}  booking_id={e.get('booking_id')}  id={e['id']}")
        if fix:
            ids = [e["id"] for e in extras]
            r = await db.seat_locks.update_many(
                {"id": {"$in": ids}}, {"$set": {"status": "released"}},
            )
            print(f"     ✂️  released {r.modified_count} extra lock(s).")
            fixed += r.modified_count

    print()
    if fix:
        print(f"Done. Released {fixed} extra lock(s). Restart the backend so the")
        print("uniq_bus_instance_seat_active index can now build:")
        print("  sudo systemctl restart starqistna-backend")
    else:
        print(f"Read-only run. Re-run with --fix to release the extra locks.")
        print("Any booking whose lock was released will need manual reconciliation.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Find/fix duplicate seat_locks blocking the new index build.")
    ap.add_argument("--release-expired", action="store_true",
                    help="First mark any expired 'locked' rows as 'released' (safe).")
    ap.add_argument("--fix", action="store_true",
                    help="Keep the best lock per (bus_instance,seat), release the rest.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.release_expired, args.fix)))
