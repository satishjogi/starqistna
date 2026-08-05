"""Signature-variant sweep against the live TBS test endpoint.

Fires `queryOnlineQR` four times, each with a different signature formula.
Whichever comes back with anything OTHER than [5] Invalid Signature is the
formula TBS actually uses on their server.

Zero side-effects: `queryOnlineQR` only READS state — no bookings created.

Usage (on the whitelisted VPS):
    cd ~/app/backend
    source .venv/bin/activate
    python scripts/gohub_sign_sweep.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND / ".env")

import httpx  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

MY_TZ = timezone(timedelta(hours=8))
CTS_NAMESPACE = "https://eticketing.tbsbts.com.my/ws_cts"
_NS = {"soap": "http://schemas.xmlsoap.org/soap/envelope/", "cts": CTS_NAMESPACE}


def _md5(v: str, upper: bool = True) -> str:
    d = hashlib.md5(v.encode("utf-8")).hexdigest()
    return d.upper() if upper else d


def _variants(ota: str, pw: str) -> list[tuple[str, str]]:
    now_my = datetime.now(MY_TZ)
    ymd = now_my.strftime("%Y%m%d")
    dmy_slash = now_my.strftime("%d/%m/%Y")
    dmy = now_my.strftime("%d%m%Y")
    return [
        ("OTA + YYYYMMDD + PWD  (upper)", _md5(f"{ota}{ymd}{pw}", True)),
        ("OTA + YYYYMMDD + PWD  (lower)", _md5(f"{ota}{ymd}{pw}", False)),
        ("OTA + DD/MM/YYYY + PWD (upper)", _md5(f"{ota}{dmy_slash}{pw}", True)),
        ("OTA + DDMMYYYY + PWD  (upper)", _md5(f"{ota}{dmy}{pw}", True)),
        ("PWD + OTA + YYYYMMDD  (upper)", _md5(f"{pw}{ota}{ymd}", True)),
        ("OTA + PWD + YYYYMMDD  (upper)", _md5(f"{ota}{pw}{ymd}", True)),
    ]


def _envelope(signature: str, ota: str, operator: str, trans_id: str) -> bytes:
    """Build a queryOnlineQR envelope with the chosen signature."""
    body = (
        f"<signature>{signature}</signature>"
        f"<ota_code>{ota}</ota_code>"
        f"<operator_code>{operator}</operator_code>"
        f"<trans_id>{trans_id}</trans_id>"
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        f'<queryOnlineQR xmlns="{CTS_NAMESPACE}">{body}</queryOnlineQR>'
        "</soap:Body></soap:Envelope>"
    )
    return xml.encode("utf-8")


def _parse_status(xml_text: str) -> tuple[str, str]:
    """Extract (code, msg) from the CTS response."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ("PARSE", xml_text[:120])
    status = root.find(".//cts:queryOnlineQR_status", _NS)
    if status is None:
        return ("NO_STATUS", xml_text[:120])
    return (status.attrib.get("code", "?"), status.attrib.get("msg", ""))


async def _try(client: httpx.AsyncClient, url: str, signature: str,
                ota: str, operator: str) -> tuple[str, str]:
    trans_id = f"SIGSWEEP-{uuid.uuid4().hex[:8].upper()}"
    body = _envelope(signature, ota, operator, trans_id)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{CTS_NAMESPACE}/queryOnlineQR"',
    }
    r = await client.post(url, content=body, headers=headers, timeout=15)
    return _parse_status(r.text)


async def _main() -> None:
    url = os.environ.get("GOHUB_BASE_URL", "").strip()
    ota = os.environ.get("GOHUB_OTA_CODE", "").strip()
    pw = os.environ.get("GOHUB_OTA_PASSWORD", "").strip()
    operator = os.environ.get("GOHUB_OPERATOR_CODE", "").strip()

    if not (url and ota and pw and operator):
        print("❌ Missing GOHUB_* env vars in backend/.env")
        return

    print(f"Endpoint: {url}")
    print(f"OTA:      {ota}   Operator: {operator}")
    print()
    print(f"{'Variant':<38}  {'Code':<6}  Message")
    print("-" * 90)

    async with httpx.AsyncClient() as client:
        for label, sig in _variants(ota, pw):
            try:
                code, msg = await _try(client, url, sig, ota, operator)
            except Exception as e:
                code, msg = ("EXC", str(e)[:80])
            marker = "  ← LIKELY WINNER" if code != "5" and code != "EXC" else ""
            print(f"{label:<38}  {code:<6}  {msg}{marker}")

    print()
    print("Reading the results:")
    print("  code=5  Invalid Signature — TBS rejected this formula")
    print("  code=1  Transaction ID cannot be found — GOOD, signature was accepted!")
    print("          (we sent a fake trans_id on purpose)")
    print("  code=4  Invalid Server IP — VPS not whitelisted")
    print("  code=0  Success (unlikely because trans_id is fake)")


if __name__ == "__main__":
    asyncio.run(_main())
