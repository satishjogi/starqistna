"""Sweep candidate destination counter codes against TBS.

We know ``from=TBS`` is accepted (past code 14). This script fires
``reserveOnlineQR_V2`` for a range of common Malaysian destination codes
to find the ones with configured rates.

Interpretation of each response code:
  * 12 "Online QR rate cannot be found"      → route pair not in rate table
  * 7  "Destination cannot be found"          → destination code invalid entirely
  *  1 "Route ID cannot be found"             → route exists but not for this trip_no
  * 10 "Trip details cannot be found"         → trip_no unknown
  *  0 "Success"                              → 🎉 we can create a real reservation

The moment we see anything OTHER than 7 / 12, we've found a live route.

Usage:
    cd ~/app/backend
    source .venv/bin/activate
    python scripts/gohub_dest_sweep.py --trip-no SQ001
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND / ".env")

# Enable GoHub for the sweep — .env may still have the flag off.
os.environ["GOHUB_ENABLED"] = "true"


# A grab-bag of destination codes seen in Malaysian bus terminal networks.
# Extend this list once TBS confirms the naming convention they use.
CANDIDATES = [
    # Major destinations
    "MLK", "MLKSEN", "MELAKA", "MKA",
    "JB", "JBS", "LARKIN", "JOHOR",
    "PEN", "PENANG", "SBS", "SUNGAI",
    "IPH", "IPOH",
    "KUANTAN", "KTN",
    "KOTA", "KTB", "KTBHARU",
    "KLIA", "KLIA1", "KLIA2",
    "GEN", "GENTING", "GHR",
    "GMC", "GMS", "GENMLK",  # keep the one we tried
    "ALOR", "AS", "ASETAR",
    # Common pattern: state / region abbreviations
    "SGB", "PLB", "TRG", "SRW",
    # Fallbacks just in case
    "T01", "T02", "T03",
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trip-no", default="SQ001")
    parser.add_argument("--from-counter", default="TBS")
    parser.add_argument("--seat", default="1A")
    parser.add_argument("--boarding-date", default=None,
                        help="YYYYMMDD or DD/MM/YYYY. Defaults to today MY.")
    parser.add_argument("--boarding-time", default="2330")
    args = parser.parse_args()

    from gohub_client import GoHubClient, GoHubError, make_opetickno  # noqa: E402

    my_today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    date = args.boarding_date or my_today

    client = GoHubClient(db=None, timeout=15)
    print(f"Origin (from) : {args.from_counter}")
    print(f"Trip no       : {args.trip_no}   Boarding: {date} {args.boarding_time}")
    print(f"Testing {len(CANDIDATES)} destination codes…\n")
    print(f"{'Dest':<12}  {'Code':<6}  Message")
    print("-" * 70)

    sem = asyncio.Semaphore(3)
    results: list[tuple[str, str, str]] = []
    hits: list[tuple[str, str, str]] = []

    async def _try(dest: str) -> None:
        async with sem:
            trans_id = f"DSWEEP-{uuid.uuid4().hex[:8].upper()}"
            opetickno = make_opetickno(trans_id[-8:], args.seat)
            seat = {
                "opetickno": opetickno, "seatno": args.seat,
                "seattype": "A", "sprice": 55.0,
                "name": "Sweep Test", "ic": "", "contact": "",
            }
            try:
                out = await client.reserve_qr(
                    trans_id=trans_id, trip_no=args.trip_no,
                    trip_date=date, depart_date=date, depart_time=args.boarding_time,
                    from_counter=args.from_counter, to_counter=dest,
                    seats=[seat],
                )
                # Success (dry-run or real).
                code = out.get("status_code", "?")
                msg = out.get("status_msg", "")
                results.append((dest, code, msg))
                hits.append((dest, code, msg))
            except GoHubError as e:
                results.append((dest, e.code, e.message))
                # 7 / 12 = expected rejections for wrong codes.
                if e.code not in ("7", "12"):
                    hits.append((dest, e.code, e.message))

    await asyncio.gather(*(_try(d) for d in CANDIDATES))

    # Print results in the order they were sent.
    for dest, code, msg in results:
        marker = "  ← 🎯" if (dest, code, msg) in hits else ""
        print(f"{dest:<12}  {code:<6}  {msg}{marker}")

    print()
    print("=" * 70)
    if hits:
        print(f"🎯 {len(hits)} candidate destination(s) worth investigating:")
        for dest, code, msg in hits:
            print(f"   {dest:<12}  [{code}] {msg}")
        print()
        print("→ Any dest above with code 0/1/10 is a real route. Rerun the probe:")
        print(f"     python scripts/gohub_probe.py --from-counter {args.from_counter} "
              f"--to-counter <that_code> --trip-no {args.trip_no} --confirm")
    else:
        print("❌ All destinations returned 7 or 12 (unknown code / no rate).")
        print("   Ask TBS support for the exact CTS destination codes assigned")
        print("   to Star Qistna's approved routes.")


if __name__ == "__main__":
    asyncio.run(main())
