"""Standalone CTS OnlineQR probe.

Runs a full reserve → confirm → query → cancel cycle against the TBS test
endpoint using the creds in `backend/.env`. Use it on the whitelisted VPS
to verify the whole integration path works end-to-end.

Usage (on the VPS after `git pull`):

    cd ~/app/backend
    source .venv/bin/activate         # or wherever your venv lives
    python scripts/gohub_probe.py

Flags:
  --trip-no SQ001         Trip number sent to CTS (must be one TBS knows).
  --boarding-date YYYYMMDD  Defaults to today (MY local).
  --boarding-time HHMM    Defaults to 2330.
  --seat 1A               Seat number.
  --from-counter TBS01    From-counter code (issued by TBS per operator/trip).
  --to-counter GMC01      To-counter code.
  --ic-no 900101-14-5555  Optional Malaysian IC (recommended for CTS records).
  --confirm               Also run confirmOnlineQR_V2 after reserve.
  --cancel                Also cancel the confirmed ticket at the end.

Every operation is printed to stdout with the parsed response so you can
share a screenshot back to TBS if anything fails.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve().parent.parent  # backend/
    load_dotenv(here / ".env")
    # We need GOHUB_ENABLED=true to actually hit the wire (even for a probe).
    if os.environ.get("GOHUB_ENABLED", "").lower() not in ("true", "1", "yes"):
        print("• Auto-enabling GOHUB (script scope only). Set GOHUB_ENABLED=true in .env for admin endpoint access.")
        os.environ["GOHUB_ENABLED"] = "true"


def _parse_args() -> argparse.Namespace:
    my_today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    p = argparse.ArgumentParser(description="Probe the TBS CTS OnlineQR test endpoint.")
    p.add_argument("--trip-no", default="SQ001")
    p.add_argument("--boarding-date", default=my_today)
    p.add_argument("--boarding-time", default="2330")
    p.add_argument("--seat", default="1A")
    p.add_argument("--seat-type", default="A", choices=["A", "C", "S", "O"])
    p.add_argument("--from-counter", default="TBS01",
                   help="From-counter code (issued by TBS per operator).")
    p.add_argument("--to-counter", default="GMC01")
    p.add_argument("--ic-no", default="")
    p.add_argument("--contact", default="")
    p.add_argument("--confirm", action="store_true",
                   help="Also run confirmOnlineQR_V2 after successful reserve.")
    p.add_argument("--cancel", action="store_true",
                   help="Also cancel the confirmed ticket at the end.")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from gohub_client import GoHubClient, GoHubError  # after env is loaded

    trans_id = f"PROBE-{uuid.uuid4().hex[:12].upper()}"
    print(f"• Endpoint: {os.environ.get('GOHUB_BASE_URL')}")
    print(f"• OTA:      {os.environ.get('GOHUB_OTA_CODE')} / operator: {os.environ.get('GOHUB_OPERATOR_CODE')}")
    print(f"• TransID:  {trans_id}")
    print()

    client = GoHubClient(db=None, timeout=20)

    # 1) reserveOnlineQR_V2
    print("1/3  reserveOnlineQR_V2 …")
    try:
        reserve = await client.reserve_qr(
            trans_id=trans_id,
            trip_no=args.trip_no,
            trip_date=args.boarding_date,
            depart_date=args.boarding_date,
            depart_time=args.boarding_time,
            from_counter=args.from_counter,
            to_counter=args.to_counter,
            seat_number=args.seat,
            seat_type=args.seat_type,
            sprice=55.0,
            passenger_name="Test Passenger",
            ic_no=args.ic_no,
            contact_no=args.contact,
        )
        print("    OK ✓")
        for k, v in reserve.items():
            print(f"       {k}: {v}")
    except GoHubError as e:
        print(f"    FAIL ✗  [{e.code}] {e.message}")
        if e.raw:
            print(f"    RAW:\n{e.raw[:800]}")
        return 1

    reserved_id = reserve.get("reservedid") or reserve.get("ReservedID") or reserve.get("reserved_id")
    if not reserved_id:
        print("    WARN: no reservedid in response — cannot confirm.")
        return 0

    # 2) confirmOnlineQR_V2 (optional)
    opetickno = None
    if args.confirm:
        print("\n2/3  confirmOnlineQR_V2 …")
        try:
            confirm = await client.confirm_qr(reserved_id=reserved_id)
            print("    OK ✓")
            for k, v in confirm.items():
                print(f"       {k}: {v}")
            opetickno = confirm.get("opetickno") or confirm.get("newopetickno")
        except GoHubError as e:
            print(f"    FAIL ✗  [{e.code}] {e.message}")
            return 2
    else:
        print("\n2/3  confirmOnlineQR_V2 skipped (use --confirm to run).")

    # 3) queryOnlineQR (round-trip verification — uses trans_id, not opetickno)
    print("\n3/3  queryOnlineQR …")
    try:
        q = await client.query_qr(trans_id=trans_id)
        print("    OK ✓")
        for k, v in q.items():
            print(f"       {k}: {v}")
    except GoHubError as e:
        print(f"    FAIL ✗  [{e.code}] {e.message}")

    if args.cancel and opetickno:
        print("\n4/4  cancelOnlineQR …")
        try:
            c = await client.cancel_qr(trans_id=trans_id, opetickno=opetickno)
            print("    OK ✓")
            for k, v in c.items():
                print(f"       {k}: {v}")
        except GoHubError as e:
            print(f"    FAIL ✗  [{e.code}] {e.message}")

    return 0


def main() -> None:
    _load_env()
    args = _parse_args()
    rc = asyncio.run(_run(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
