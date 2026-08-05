"""Exhaustive signature-variant sweep against TBS test endpoint.

Tries every realistic combination of:
  * hash algorithm  (md5, sha1, sha256)
  * password source (GOHUB_OTA_PASSWORD, GOHUB_OPERATOR_PASSWORD)
  * date format     (YYYYMMDD, DD/MM/YYYY, DDMMYYYY)
  * field order     (ota+date+pwd, pwd+ota+date, ota+pwd+date, +operator variants)
  * output case     (upper / lower hex)

Sends each as a harmless `queryOnlineQR` (read-only) and prints the ones
whose response does NOT contain "Invalid Signature".

Usage (whitelisted VPS):
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


def _hash(algo: str, v: str, upper: bool) -> str:
    d = hashlib.new(algo, v.encode("utf-8")).hexdigest()
    return d.upper() if upper else d


def _build_variants(ota: str, ota_pw: str, operator: str, op_pw: str) -> list[tuple[str, str]]:
    """Return a list of (label, signature_hex) pairs to try."""
    now = datetime.now(MY_TZ)
    dates = {
        "YMD": now.strftime("%Y%m%d"),
        "DMY/": now.strftime("%d/%m/%Y"),
        "DMY": now.strftime("%d%m%Y"),
    }
    passwords = {"OTA_PW": ota_pw, "OP_PW": op_pw} if op_pw else {"OTA_PW": ota_pw}
    orderings = [
        # label,  lambda(a, d, p, op) -> raw
        ("O+D+P",   lambda a, d, p, op: f"{a}{d}{p}"),
        ("O+P+D",   lambda a, d, p, op: f"{a}{p}{d}"),
        ("P+O+D",   lambda a, d, p, op: f"{p}{a}{d}"),
        ("D+O+P",   lambda a, d, p, op: f"{d}{a}{p}"),
        ("O+op+D+P", lambda a, d, p, op: f"{a}{op}{d}{p}"),
        ("O+D+op+P", lambda a, d, p, op: f"{a}{d}{op}{p}"),
    ]
    algos = ["md5", "sha1", "sha256"]
    variants: list[tuple[str, str]] = []
    for algo in algos:
        for date_name, date_val in dates.items():
            for pw_name, pw_val in passwords.items():
                for ord_name, ord_fn in orderings:
                    raw = ord_fn(ota, date_val, pw_val, operator)
                    for upper in (True, False):
                        case = "UP" if upper else "lo"
                        label = f"{algo:6} {ord_name:9} {date_name:5} {pw_name:6} {case}"
                        variants.append((label, _hash(algo, raw, upper)))
    return variants


def _envelope(signature: str, ota: str, operator: str, trans_id: str) -> bytes:
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
    trans_id = f"SW-{uuid.uuid4().hex[:8].upper()}"
    body = _envelope(signature, ota, operator, trans_id)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{CTS_NAMESPACE}/queryOnlineQR"',
    }
    try:
        r = await client.post(url, content=body, headers=headers, timeout=15)
        return _parse_status(r.text)
    except Exception as e:
        return ("EXC", str(e)[:80])


async def _main() -> None:
    url = os.environ.get("GOHUB_BASE_URL", "").strip()
    ota = os.environ.get("GOHUB_OTA_CODE", "").strip()
    ota_pw = os.environ.get("GOHUB_OTA_PASSWORD", "").strip()
    operator = os.environ.get("GOHUB_OPERATOR_CODE", "").strip()
    op_pw = os.environ.get("GOHUB_OPERATOR_PASSWORD", "").strip()

    if not (url and ota and ota_pw and operator):
        print("❌ Missing GOHUB_* env vars in backend/.env")
        return

    print(f"Endpoint : {url}")
    print(f"OTA      : {ota}")
    print(f"Operator : {operator}")
    print(f"OTA pw   : len={len(ota_pw)}  operator pw: len={len(op_pw) if op_pw else 0}")
    print()

    variants = _build_variants(ota, ota_pw, operator, op_pw)
    print(f"Testing {len(variants)} signature variants (throttled to 5 concurrent)…\n")

    winners: list[tuple[str, str, str]] = []
    all_rows: list[tuple[str, str, str]] = []
    sem = asyncio.Semaphore(5)

    async with httpx.AsyncClient() as client:
        async def _run(label: str, sig: str):
            async with sem:
                code, msg = await _try(client, url, sig, ota, operator)
                is_signature_error = "invalid signature" in msg.lower()
                row = (label, code, msg)
                all_rows.append(row)
                if not is_signature_error and code not in ("EXC", "PARSE", "NO_STATUS"):
                    winners.append(row)

        await asyncio.gather(*(_run(label, sig) for label, sig in variants))

    print("=" * 90)
    if winners:
        print(f"🎯 {len(winners)} variant(s) ACCEPTED by TBS (signature valid):")
        print("=" * 90)
        for label, code, msg in winners:
            print(f"  ✓ {label}   →  [{code}] {msg}")
        print()
        print("→ Use the winner's algo + order + date + password source in gohub_client.py")
    else:
        print("❌ Every variant returned 'Invalid Signature'. Likely causes:")
        print("   1. Password in backend/.env doesn't match what TBS provisioned")
        print("   2. TBS uses a bespoke formula not covered by this sweep")
        print("   3. Escape / trim issue in the password value")
        print()
        print("Next step: email TBS support with the OTA code + a request for the exact")
        print("signature formula (algo, field order, date format).")
        print()
        print(f"(Ran {len(all_rows)} variants — sample rejection:")
        if all_rows:
            l, c, m = all_rows[0]
            print(f"    {l}  →  [{c}] {m})")


if __name__ == "__main__":
    asyncio.run(_main())
