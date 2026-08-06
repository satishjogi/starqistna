"""Force-reconcile a stuck Stripe payment.

Usage:
    # Reconcile ALL stuck initiated transactions (older than 30s):
    python scripts/reconcile_payments.py

    # Reconcile a specific Stripe checkout session:
    python scripts/reconcile_payments.py --session cs_live_XYZ...

    # Reconcile a specific booking id:
    python scripts/reconcile_payments.py --booking BK123

    # Dry-run (show what would happen, don't touch DB):
    python scripts/reconcile_payments.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND / ".env")

import server  # noqa: E402


async def _run(args: argparse.Namespace) -> int:
    db = server.db

    if args.session:
        txn = await db.payment_transactions.find_one({"session_id": args.session}, {"_id": 0})
        if not txn:
            print(f"❌ No transaction found for session {args.session}")
            return 1
        target_txns = [txn]
    elif args.booking:
        txn = await db.payment_transactions.find_one({"booking_id": args.booking}, {"_id": 0})
        if not txn:
            print(f"❌ No transaction found for booking {args.booking}")
            return 1
        target_txns = [txn]
    else:
        # Everything stuck
        cursor = db.payment_transactions.find({
            "booking_finalized": {"$ne": True},
            "payment_status": {"$ne": "paid"},
        }, {"_id": 0})
        target_txns = [t async for t in cursor]

    print(f"Found {len(target_txns)} candidate transaction(s).")
    if args.dry_run:
        for t in target_txns:
            print(f"  DRY  {t.get('session_id')}  booking={t.get('booking_id')}  "
                  f"status={t.get('status')}  payment_status={t.get('payment_status')}")
        return 0

    finalized = 0
    for t in target_txns:
        result = await server._reconcile_one_transaction(t)
        action = result.get("action", "?")
        icon = {"finalized": "✅", "already_finalized": "•", "still_pending": "⏳",
                "stripe_error": "⚠️ ", "skip_missing_session_id": "-"}.get(action, "?")
        print(f"  {icon} {result}")
        if action == "finalized":
            finalized += 1

    print(f"\nDone. Finalized {finalized} of {len(target_txns)} transaction(s).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--session", help="Stripe checkout session ID to reconcile")
    parser.add_argument("--booking", help="Booking ID to reconcile")
    parser.add_argument("--dry-run", action="store_true", help="Show candidates without touching Stripe")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
