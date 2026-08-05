"""Print the CTS signature we would send, plus alternative variants.

Copy the output and share with TBS support so they can tell us which
variant they expect.  Reads OTA code + password from backend/.env
(never printed in the clear — only lengths + a masked preview).

Usage:
    cd ~/app/backend
    source .venv/bin/activate
    python scripts/gohub_sign_debug.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make gohub_client importable + load .env.
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND / ".env")

MY_TZ = timezone(timedelta(hours=8))
UTC = timezone.utc


def _mask(s: str) -> str:
    if not s:
        return "(empty)"
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def _md5(v: str, upper: bool = True) -> str:
    d = hashlib.md5(v.encode("utf-8")).hexdigest()
    return d.upper() if upper else d


def main() -> None:
    ota = os.environ.get("GOHUB_OTA_CODE", "").strip()
    pw = os.environ.get("GOHUB_OTA_PASSWORD", "").strip()

    now_my = datetime.now(MY_TZ)
    now_utc = datetime.now(UTC)
    my_date = now_my.strftime("%Y%m%d")

    print("=" * 70)
    print(" CTS Signature diagnostic")
    print("=" * 70)
    print(f"  VPS local time (UTC) : {now_utc.strftime('%Y-%m-%d %H:%M:%S %z')}")
    print(f"  Malaysia local time  : {now_my.strftime('%Y-%m-%d %H:%M:%S %z')}")
    print(f"  MY date (YYYYMMDD)   : {my_date}")
    print()
    print(f"  OTA code              : {ota!r}")
    print(f"  OTA password (len)    : {len(pw)}  -> {_mask(pw)}")
    if pw and (pw != pw.strip()):
        print("  ⚠ Password has leading/trailing whitespace — that's a common cause of Invalid Signature.")
    print()

    if not ota or not pw:
        print("❌ OTA code or password is empty. Fix backend/.env and re-run.")
        return

    # Variant 1: OTA + YYYYMMDD + PASSWORD (uppercase MD5)  ← current impl
    v1 = f"{ota}{my_date}{pw}"
    print("Current implementation  (OTA + YYYYMMDD + PASSWORD, MD5 upper):")
    print(f"  raw  : {ota}{my_date}<PASSWORD>")
    print(f"  md5U : {_md5(v1, True)}")
    print(f"  md5L : {_md5(v1, False)}")
    print()

    # Variant 2: same but DD/MM/YYYY date
    dmy = now_my.strftime("%d/%m/%Y")
    v2 = f"{ota}{dmy}{pw}"
    print("Variant A  (OTA + DD/MM/YYYY + PASSWORD):")
    print(f"  raw  : {ota}{dmy}<PASSWORD>")
    print(f"  md5U : {_md5(v2, True)}")
    print(f"  md5L : {_md5(v2, False)}")
    print()

    # Variant 3: DDMMYYYY
    ddmmyyyy = now_my.strftime("%d%m%Y")
    v3 = f"{ota}{ddmmyyyy}{pw}"
    print("Variant B  (OTA + DDMMYYYY + PASSWORD):")
    print(f"  raw  : {ota}{ddmmyyyy}<PASSWORD>")
    print(f"  md5U : {_md5(v3, True)}")
    print(f"  md5L : {_md5(v3, False)}")
    print()

    # Variant 4: PASSWORD + OTA + DATE (some vendors swap order)
    v4 = f"{pw}{ota}{my_date}"
    print("Variant C  (PASSWORD + OTA + YYYYMMDD):")
    print(f"  md5U : {_md5(v4, True)}")
    print()

    print("=" * 70)
    print(" Share this file (WITHOUT the password value) with TBS support")
    print(" and ask which signature format their server expects.")
    print("=" * 70)


if __name__ == "__main__":
    main()
