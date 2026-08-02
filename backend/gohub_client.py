"""GoHub / CTS OnlineQR SOAP client (for TBS boarding pass QR generation).

Spec: BTS CTS-Operator Web Service Specification (OnlineQR) v1.2.11
Endpoints:
  Test: http://47.254.200.95/ws_cts_v2_TBS_test/serviceQR.asmx
  Live: https://eticketing.tbsbts.com.my/ws_cts_v2/serviceQR.asmx

Authentication
--------------
Every request carries an MD5 signature computed as:
    md5(OTACode + TodayDate + OTAPassword)
where TodayDate is Malaysia local date `YYYYMMDD`.

The signature is passed alongside OTA+Operator codes in the SOAP envelope.
This module handles the signature, envelope construction, HTTP POST, and
response parsing. Callers only pass business fields.

Design notes
------------
* We do NOT depend on `zeep` — TBS's WSDL is quirky and we only need 5 ops.
  Raw XML templates via `httpx` are more predictable and easier to mock.
* All calls are async so they fit our FastAPI event loop.
* Every request/response pair is persisted to `gohub_logs` for audit &
  post-mortem debugging. Never rely on your app logs alone for a paid API.
* When `GOHUB_ENABLED=false` (default), every operation short-circuits
  with a dry-run payload — safe for staging + local dev.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# TBS + CTS run on Malaysia time — signatures MUST use MY local date.
MY_TZ = timezone(timedelta(hours=8))

# SOAP namespace declared by every CTS op.
_NS = {"soap": "http://schemas.xmlsoap.org/soap/envelope/", "cts": "http://tempuri.org/"}


class GoHubError(RuntimeError):
    """Raised when the CTS API returns a non-OK status or the transport fails."""

    def __init__(self, code: str, message: str, raw: Optional[str] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.raw = raw


def _today_my_date() -> str:
    """Malaysia local date as `YYYYMMDD` — used in the auth signature."""
    return datetime.now(MY_TZ).strftime("%Y%m%d")


def _md5_signature(ota_code: str, ota_password: str) -> str:
    """Compute md5(OTACode + TodayDate + OTAPassword) per CTS spec §3.2."""
    raw = f"{ota_code}{_today_my_date()}{ota_password}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _xml_escape(v: Any) -> str:
    if v is None:
        return ""
    s = str(v)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _envelope(operation: str, body: dict) -> bytes:
    """Build a minimal SOAP 1.1 envelope for a CTS operation."""
    inner = "".join(f"<{k}>{_xml_escape(v)}</{k}>" for k, v in body.items() if v is not None)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        f'<{operation} xmlns="http://tempuri.org/">{inner}</{operation}>'
        "</soap:Body></soap:Envelope>"
    )
    return xml.encode("utf-8")


def _parse_response(operation: str, xml_body: str) -> dict:
    """Extract the CTS `<{operation}Result>` payload as a dict.

    CTS returns a `<StatusCode>` + `<StatusDescription>` inside the result.
    Anything other than "00" raises GoHubError so the caller sees clean
    exceptions instead of parsing XML themselves.
    """
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError as e:
        raise GoHubError("PARSE_ERROR", f"Malformed XML: {e}", raw=xml_body[:500]) from e

    result = root.find(f".//cts:{operation}Result", _NS)
    if result is None:
        # SOAP fault path — CTS uses <faultstring>.
        fault = root.find(".//soap:Fault/faultstring", _NS)
        if fault is not None:
            raise GoHubError("SOAP_FAULT", (fault.text or "").strip(), raw=xml_body[:500])
        raise GoHubError("NO_RESULT", f"Missing {operation}Result", raw=xml_body[:500])

    payload = {child.tag.split("}")[-1]: (child.text or "").strip() for child in result}
    code = payload.get("StatusCode") or payload.get("statusCode") or ""
    if code and code != "00":
        raise GoHubError(code, payload.get("StatusDescription") or "CTS returned non-OK status",
                         raw=xml_body[:500])
    return payload


class GoHubClient:
    """Async CTS OnlineQR client. Instantiate once per app and reuse.

    Reads configuration from env vars — never hard-code them:
      * GOHUB_ENABLED      — feature flag, "true"/"false"
      * GOHUB_BASE_URL     — SOAP endpoint (test or live)
      * GOHUB_OTA_CODE     — Star Qistna's OTA identity (STARQISTINA)
      * GOHUB_OTA_PASSWORD — OTA password for signature
      * GOHUB_OPERATOR_CODE     — operator identity inside TBS (QISTINA)
      * GOHUB_OPERATOR_PASSWORD — operator password (kept for future ops
                                  that need re-auth — currently unused in
                                  signature but persisted in audit logs).
    """

    def __init__(self, db=None, timeout: float = 20.0):
        self.enabled = os.environ.get("GOHUB_ENABLED", "false").strip().lower() in ("1", "true", "yes")
        self.base_url = os.environ.get("GOHUB_BASE_URL", "").strip()
        self.ota_code = os.environ.get("GOHUB_OTA_CODE", "").strip()
        self.ota_password = os.environ.get("GOHUB_OTA_PASSWORD", "").strip()
        self.operator_code = os.environ.get("GOHUB_OPERATOR_CODE", "").strip()
        self.timeout = timeout
        self.db = db  # motor db — used for audit logging

    # ---- Public operations ----------------------------------------------------

    async def reserve_qr(self, *, trans_id: str, trip_no: str, boarding_date: str,
                          boarding_time: str, seat_number: str, seat_type: str = "A",
                          from_counter: str, to_counter: str, ic_no: Optional[str] = None,
                          contact_no: Optional[str] = None) -> dict:
        """`reserveOnlineQR_V2` — soft-hold a QR ticket for ~1 hour.

        Follow up with `confirm_qr(trans_id, reserved_id)` after payment
        succeeds. If you skip confirm within the TTL, TBS auto-releases.
        """
        return await self._call("reserveOnlineQR_V2", {
            "OTACode": self.ota_code,
            "MD5Sign": _md5_signature(self.ota_code, self.ota_password),
            "OperatorCode": self.operator_code,
            "TransID": trans_id,
            "TripNo": trip_no,
            "BoardingDate": boarding_date,   # YYYYMMDD
            "BoardingTime": boarding_time,   # HHmm
            "SeatNo": seat_number,
            "SeatType": seat_type,           # A/C/S/O per CTS spec
            "FromCounter": from_counter,
            "ToCounter": to_counter,
            "ICNo": ic_no or "",
            "ContactNo": contact_no or "",
        })

    async def confirm_qr(self, *, trans_id: str, reserved_id: str) -> dict:
        """`confirmOnlineQR_V2` — commit a previously-reserved ticket."""
        return await self._call("confirmOnlineQR_V2", {
            "OTACode": self.ota_code,
            "MD5Sign": _md5_signature(self.ota_code, self.ota_password),
            "OperatorCode": self.operator_code,
            "TransID": trans_id,
            "ReservedID": reserved_id,
        })

    async def cancel_qr(self, *, trans_id: str, opetickno: str) -> dict:
        """`cancelOnlineQR` — void a confirmed ticket. Response includes refund_amount."""
        return await self._call("cancelOnlineQR", {
            "OTACode": self.ota_code,
            "MD5Sign": _md5_signature(self.ota_code, self.ota_password),
            "OperatorCode": self.operator_code,
            "TransID": trans_id,
            "OpeTickNo": opetickno,
        })

    async def query_qr(self, *, opetickno: str) -> dict:
        """`queryOnlineQR` — reconcile a single ticket's current status."""
        return await self._call("queryOnlineQR", {
            "OTACode": self.ota_code,
            "MD5Sign": _md5_signature(self.ota_code, self.ota_password),
            "OperatorCode": self.operator_code,
            "OpeTickNo": opetickno,
        })

    # ---- Transport ------------------------------------------------------------

    async def _call(self, operation: str, body: dict) -> dict:
        """Send a SOAP request, parse, and persist an audit record."""
        started = datetime.now(timezone.utc)
        request_xml = _envelope(operation, body)

        if not self.enabled or not self.base_url:
            payload = {"dry_run": True, "note": "GOHUB_ENABLED=false; returning mocked OK.",
                       "StatusCode": "00", "StatusDescription": "OK (dry-run)"}
            await self._audit_log(operation, body, request_xml, payload, None, started, dry_run=True)
            return payload

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"http://tempuri.org/{operation}"',
        }
        response_text = ""
        error: Optional[GoHubError] = None
        payload: dict = {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.base_url, content=request_xml, headers=headers)
            response_text = resp.text
            if resp.status_code >= 400:
                error = GoHubError(f"HTTP_{resp.status_code}",
                                    f"CTS returned {resp.status_code}", raw=response_text[:500])
                raise error
            payload = _parse_response(operation, response_text)
        except httpx.HTTPError as e:
            error = GoHubError("TRANSPORT_ERROR", str(e))
            raise error
        finally:
            await self._audit_log(operation, body, request_xml, payload, error, started,
                                   response_text=response_text)
        return payload

    async def _audit_log(self, operation: str, body: dict, request_xml: bytes,
                          response_payload: dict, error: Optional[GoHubError],
                          started: datetime, response_text: str = "",
                          dry_run: bool = False) -> None:
        """Insert a full request/response record into `gohub_logs` for audits."""
        if self.db is None:
            return
        try:
            # Never persist the raw password anywhere — redact from the body copy.
            safe_body = {k: ("***" if re.search(r"password", k, re.I) else v) for k, v in body.items()}
            doc = {
                "operation": operation,
                "started_at": started.isoformat(),
                "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                "request": safe_body,
                "response": response_payload,
                "response_raw": response_text[:4000] if response_text else "",
                "dry_run": dry_run,
                "error": None if error is None else {"code": error.code, "message": error.message},
                "endpoint": self.base_url or None,
            }
            await self.db.gohub_logs.insert_one(doc)
        except Exception:  # noqa: BLE001 — audit failures MUST NOT break the caller.
            logger.exception("gohub_logs insert failed for %s", operation)


# Helper for callers that don't want to instantiate a client on every request.
_default_client: Optional[GoHubClient] = None


def get_default_client(db) -> GoHubClient:
    """Return a lazily-created singleton bound to the given Motor db."""
    global _default_client
    if _default_client is None:
        _default_client = GoHubClient(db=db)
    return _default_client
