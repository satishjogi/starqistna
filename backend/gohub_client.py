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

# CTS uses its own namespace inside the SOAP body (NOT the default tempuri).
# Kept configurable via env so we can swap between test and live without a code change.
CTS_NAMESPACE = "https://eticketing.tbsbts.com.my/ws_cts"

# SOAP namespaces used when parsing the response envelope.
_NS = {"soap": "http://schemas.xmlsoap.org/soap/envelope/", "cts": CTS_NAMESPACE}


class GoHubError(RuntimeError):
    """Raised when the CTS API returns a non-OK status or the transport fails."""

    def __init__(self, code: str, message: str, raw: Optional[str] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.raw = raw


# ---------------------------------------------------------------------------
# Format helpers — CTS spec §4 requires very specific date/time shapes.
# ---------------------------------------------------------------------------

def _today_my_date() -> str:
    """Malaysia local date as ``DD/MM/YYYY`` — the format TBS uses inside the
    signature (verified live 2026-08 via ``scripts/gohub_sign_sweep.py``).

    The spec (v1.2.11) says "MD5 Encryption based on (OTACode+Today Date+Password)"
    but is silent on the date format. Empirically TBS's server accepts only
    the slash-separated DD/MM/YYYY variant — same shape as ``trip_date`` /
    ``depart_date`` in the request body.
    """
    return datetime.now(MY_TZ).strftime("%d/%m/%Y")


def _md5_signature(ota_code: str, ota_password: str) -> str:
    """Compute md5(OTACode + TodayDate + OTAPassword) — LOWERCASE hex.

    TBS's server compares the hash as a lowercase hex string (verified live —
    the spec's own example values are lowercase too, e.g.
    ``89fa559006207c107fab09ff555f5b32``).
    """
    raw = f"{ota_code}{_today_my_date()}{ota_password}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _fmt_date_ddmmyyyy(value: str) -> str:
    """Normalize a date to `DD/MM/YYYY` (CTS spec §4, 10 chars).

    Accepts `YYYYMMDD`, `DD/MM/YYYY`, or `YYYY-MM-DD` — anything else raises.
    """
    if not value:
        raise ValueError("depart_date/trip_date is required")
    v = value.strip()
    # Already in the right shape.
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", v):
        return v
    # YYYYMMDD
    if re.fullmatch(r"\d{8}", v):
        return f"{v[6:8]}/{v[4:6]}/{v[0:4]}"
    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        y, m, d = v.split("-")
        return f"{d}/{m}/{y}"
    raise ValueError(f"Unrecognised date format: {value!r} (expected YYYYMMDD / DD/MM/YYYY / YYYY-MM-DD)")


def _fmt_time_hhmmss(value: str) -> str:
    """Normalize a time to `HHMMSS` (CTS spec §4, 6 chars, 24-hour).

    Accepts `HHMM`, `HHMMSS`, `HH:MM`, or `HH:MM:SS`.
    """
    if not value:
        raise ValueError("depart_time is required")
    v = value.strip().replace(":", "")
    if re.fullmatch(r"\d{6}", v):
        return v
    if re.fullmatch(r"\d{4}", v):
        return f"{v}00"
    raise ValueError(f"Unrecognised time format: {value!r} (expected HHMM / HHMMSS / HH:MM)")


def make_opetickno(booking_ref: str, seat_no: str) -> str:
    """Compose the operator ticket number sent to CTS.

    Format:  `SQ-{booking_ref}-{seat_no}`   (max 20 chars per CTS spec §4).

    Booking refs are 8 chars (BK + 6-char uuid slice); seat numbers are typically
    2-3 chars ("1A" .. "12D"). That keeps us well within the 20-char envelope.
    """
    if not booking_ref or not seat_no:
        raise ValueError("booking_ref and seat_no are both required for opetickno")
    tick = f"SQ-{booking_ref}-{seat_no}".upper()
    if len(tick) > 20:
        raise ValueError(f"opetickno {tick!r} exceeds 20 chars — shorten booking_ref or seat_no")
    return tick


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


def _detail_element(**attrs: Any) -> str:
    """Build a `<detail attr1="v1" attr2="v2" />` element (CTS uses attributes)."""
    kv = " ".join(f'{k}="{_xml_escape(v)}"' for k, v in attrs.items() if v is not None)
    return f"<detail {kv} />"


def _ticket_details_xml(details: list[dict]) -> str:
    """Wrap a list of `detail` dicts in `<ticket_details>...</ticket_details>`."""
    if not details:
        return ""
    inner = "".join(_detail_element(**d) for d in details)
    return f"<ticket_details>{inner}</ticket_details>"


# ---------------------------------------------------------------------------
# Seat validation — enforces CTS §4 mandatory fields BEFORE we hit the wire.
# ---------------------------------------------------------------------------

_ALLOWED_SEAT_TYPES = {"A", "C", "S", "O"}


def _validate_and_normalize_seats(
    seats: list[dict], *, require_opetickno: bool = True, require_name: bool = True
) -> list[dict]:
    """Verify every seat dict carries the fields CTS requires and coerce shapes.

    Contract per CTS §4:
      * `opetickno`  — mandatory, ≤ 20 chars
      * `seatno`     — optional (recommended), ≤ 3 chars
      * `seattype`   — optional, one of A/C/S/O
      * `sprice`     — optional numeric, formatted with 2 dp
      * `name`       — mandatory, ≤ 50 chars
      * `ic`         — optional, ≤ 14 chars
      * `contact`    — optional, ≤ 14 chars
    """
    if not seats:
        raise ValueError("seats list must not be empty")
    out: list[dict] = []
    for i, seat in enumerate(seats):
        s = dict(seat)  # copy so we don't mutate caller data
        if require_opetickno and not s.get("opetickno"):
            raise ValueError(f"seats[{i}].opetickno is required")
        if require_name and not s.get("name"):
            raise ValueError(f"seats[{i}].name is required")
        if s.get("seattype") and s["seattype"] not in _ALLOWED_SEAT_TYPES:
            raise ValueError(
                f"seats[{i}].seattype={s['seattype']!r} — must be one of A/C/S/O"
            )
        # Format sprice to 2 dp so CTS's numeric parser doesn't choke on 55 vs 55.0.
        if "sprice" in s and s["sprice"] is not None:
            s["sprice"] = f"{float(s['sprice']):.2f}"
        # Drop keys with None so we don't emit empty attributes.
        out.append({k: v for k, v in s.items() if v is not None and v != ""})
    return out


def _envelope(operation: str, body: dict, ticket_details: Optional[list[dict]] = None) -> bytes:
    """Build a minimal SOAP 1.1 envelope for a CTS operation.

    * `body` maps element names → text values (all elements). None values are
      skipped so we don't emit `<foo></foo>` for optional bits.
    * `ticket_details`, if given, is appended as a `<ticket_details><detail
      attr1="…" attr2="…" /></ticket_details>` block per CTS's WSDL — the
      `<detail>` fields are ATTRIBUTES, not child elements.
    """
    inner_parts = [f"<{k}>{_xml_escape(v)}</{k}>" for k, v in body.items() if v is not None]
    if ticket_details:
        inner_parts.append(_ticket_details_xml(ticket_details))
    inner = "".join(inner_parts)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        f'<{operation} xmlns="{CTS_NAMESPACE}">{inner}</{operation}>'
        "</soap:Body></soap:Envelope>"
    )
    return xml.encode("utf-8")


def _parse_response(operation: str, xml_body: str) -> dict:
    """Extract the CTS `<{operation}Result>` payload as a dict.

    CTS's actual response shape (discovered live 2026-02):

        <{op}Response xmlns="https://eticketing.tbsbts.com.my/ws_cts">
          <{op}Result>
            <{op}_status code="0" msg="OK">
              <{op}_details>...business fields...</{op}_details>
            </{op}_status>
          </{op}Result>
        </{op}Response>

    We flatten to `{status_code, status_msg, ...business_fields}`. Any
    non-"0" / non-"00" `code` attribute raises GoHubError so callers get
    clean exceptions.
    """
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError as e:
        raise GoHubError("PARSE_ERROR", f"Malformed XML: {e}", raw=xml_body[:500]) from e

    # SOAP fault path — CTS uses <faultstring>.
    fault = root.find(".//soap:Fault/faultstring", _NS)
    if fault is not None:
        raise GoHubError("SOAP_FAULT", (fault.text or "").strip(), raw=xml_body[:500])

    result = root.find(f".//cts:{operation}Result", _NS)
    if result is None:
        raise GoHubError("NO_RESULT", f"Missing {operation}Result", raw=xml_body[:500])

    payload: dict = {}
    status = result.find(f"./cts:{operation}_status", _NS)
    if status is not None:
        code = (status.attrib.get("code") or "").strip()
        msg = (status.attrib.get("msg") or "").strip()
        payload["status_code"] = code
        payload["status_msg"] = msg
        # Ok = "0" or "00"; anything else is an error.
        if code and code not in ("0", "00"):
            raise GoHubError(code, msg or "CTS returned non-OK status", raw=xml_body[:500])
        details = status.find(f"./cts:{operation}_details", _NS)
        if details is not None:
            for child in details:
                tag = child.tag.split("}")[-1]
                # Flatten attributes + text into the payload.
                for k, v in child.attrib.items():
                    payload[f"{tag}_{k}"] = v
                if child.text and child.text.strip():
                    payload[tag] = child.text.strip()
    else:
        # Fallback: some older ops may still return flat StatusCode children.
        payload = {c.tag.split("}")[-1]: (c.text or "").strip() for c in result}
        code = payload.get("StatusCode") or ""
        if code and code not in ("0", "00"):
            raise GoHubError(code, payload.get("StatusDescription") or "CTS returned non-OK",
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
        # CTS's separate image-with-logo endpoint (§6 of spec). Falls back to
        # the well-known gopass URL if the env var isn't overridden.
        self.qr_image_url = os.environ.get(
            "GOHUB_QR_IMAGE_URL",
            "https://gopassqr.nssit.com.my/QrCodeWithLogo",
        ).strip()
        self.ota_code = os.environ.get("GOHUB_OTA_CODE", "").strip()
        self.ota_password = os.environ.get("GOHUB_OTA_PASSWORD", "").strip()
        self.operator_code = os.environ.get("GOHUB_OPERATOR_CODE", "").strip()
        self.timeout = timeout
        self.db = db  # motor db — used for audit logging

    # ---- Public operations ----------------------------------------------------

    async def fetch_qr_image(self, qr_value: str) -> Optional[bytes]:
        """Fetch the branded (gopass-logo-embedded) PNG for a CTS QR string.

        The image endpoint is a plain HTTP GET separate from the SOAP API.
        Falls back to ``None`` on ANY failure — callers use their own locally
        generated QR image in that case. Never raises.

        Returns the raw PNG bytes on success, ``None`` on any error (network,
        non-200 HTTP, empty body, disabled feature-flag).
        """
        if not self.enabled or not qr_value or not self.qr_image_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(self.qr_image_url, params={"qrValue": qr_value})
            if r.status_code != 200:
                logger.warning(
                    "gohub: QR image fetch returned HTTP %s for qr=%s…",
                    r.status_code, qr_value[:20],
                )
                return None
            body = r.content
            # Basic sanity — a PNG must start with the 8-byte signature.
            if not body.startswith(b"\x89PNG\r\n\x1a\n"):
                logger.warning(
                    "gohub: QR image endpoint returned non-PNG (%d bytes) — falling back",
                    len(body),
                )
                return None
            return body
        except Exception as e:  # noqa: BLE001 — never break the booking flow
            logger.warning("gohub: QR image fetch failed: %s", e)
            return None

    async def get_qr(self, *, trans_id: str, trip_no: str, trip_date: str,
                      depart_date: str, depart_time: str,
                      from_counter: str, to_counter: str,
                      seats: list[dict]) -> dict:
        """`getOnlineQR_V2` — one-shot QR generation (no reserve+confirm).

        Recommended path once payment has cleared: CTS returns the CTS
        ticket_no + QR string in a single round-trip, so nothing can end up
        "reserved but unconfirmed" on our side.

        `seats` is a list of per-passenger dicts. See `_validate_and_normalize_seats`
        for the required shape (opetickno + name are mandatory).
        """
        details = _validate_and_normalize_seats(seats)
        body = {
            "signature": _md5_signature(self.ota_code, self.ota_password),
            "ota_code": self.ota_code,
            "operator_code": self.operator_code,
            "trans_id": trans_id,
            "trip_no": trip_no,
            "trip_date": _fmt_date_ddmmyyyy(trip_date),
            "depart_date": _fmt_date_ddmmyyyy(depart_date),
            "depart_time": _fmt_time_hhmmss(depart_time),
            "from": from_counter,
            "to": to_counter,
        }
        return await self._call("getOnlineQR_V2", body, ticket_details=details)

    async def reserve_qr(self, *, trans_id: str, trip_no: str, trip_date: str,
                          depart_date: str, depart_time: str,
                          from_counter: str, to_counter: str,
                          seats: list[dict]) -> dict:
        """`reserveOnlineQR_V2` — soft-hold QR tickets for ~1 hour.

        Use this when you want to lock a fare for a customer BEFORE payment.
        Follow up with `confirm_qr(reserved_id=...)` to commit.

        `seats` — one dict per passenger. See `_validate_and_normalize_seats`.
        """
        details = _validate_and_normalize_seats(seats)
        body = {
            "signature": _md5_signature(self.ota_code, self.ota_password),
            "ota_code": self.ota_code,
            "operator_code": self.operator_code,
            "trans_id": trans_id,
            "trip_no": trip_no,
            "trip_date": _fmt_date_ddmmyyyy(trip_date),
            "depart_date": _fmt_date_ddmmyyyy(depart_date),
            "depart_time": _fmt_time_hhmmss(depart_time),
            "from": from_counter,
            "to": to_counter,
        }
        return await self._call("reserveOnlineQR_V2", body, ticket_details=details)

    async def confirm_qr(self, *, reserved_id: str,
                          seats: list[dict]) -> dict:
        """`confirmOnlineQR_V2` — commit previously-reserved tickets.

        `seats` must carry `{opetickno, newopetickno}` for every reserved seat.
        The `newopetickno` is the FINAL operator ticket number CTS will echo
        back on the QR. We normally pass the same value for both.
        """
        details = []
        for i, s in enumerate(seats):
            if not s.get("opetickno"):
                raise ValueError(f"seats[{i}].opetickno is required for confirm")
            details.append({
                "opetickno": s["opetickno"],
                "newopetickno": s.get("newopetickno") or s["opetickno"],
            })
        body = {
            "signature": _md5_signature(self.ota_code, self.ota_password),
            "ota_code": self.ota_code,
            "operator_code": self.operator_code,
            "reservedid": reserved_id,
        }
        return await self._call("confirmOnlineQR_V2", body, ticket_details=details)

    async def cancel_qr(self, *, trans_id: str, opeticknos: list[str]) -> dict:
        """`cancelOnlineQR` — void confirmed tickets. Response includes refund amount.

        `opeticknos` is a list — CTS lets you cancel every seat in a booking
        with a single call by stacking multiple `<detail>` elements.
        """
        if not opeticknos:
            raise ValueError("cancel_qr requires at least one opetickno")
        body = {
            "signature": _md5_signature(self.ota_code, self.ota_password),
            "ota_code": self.ota_code,
            "operator_code": self.operator_code,
            "trans_id": trans_id,
        }
        details = [{"opetickno": t} for t in opeticknos]
        return await self._call("cancelOnlineQR", body, ticket_details=details)

    async def query_qr(self, *, trans_id: str) -> dict:
        """`queryOnlineQR` — reconcile a transaction's current ticket state.

        Note: queries are by `trans_id` (our correlation id), NOT opetickno.
        """
        body = {
            "signature": _md5_signature(self.ota_code, self.ota_password),
            "ota_code": self.ota_code,
            "operator_code": self.operator_code,
            "trans_id": trans_id,
        }
        return await self._call("queryOnlineQR", body)

    # ---- Transport ------------------------------------------------------------

    async def _call(self, operation: str, body: dict,
                     ticket_details: Optional[list[dict]] = None) -> dict:
        """Send a SOAP request, parse, and persist an audit record."""
        started = datetime.now(timezone.utc)
        request_xml = _envelope(operation, body, ticket_details=ticket_details)

        if not self.enabled or not self.base_url:
            payload = {"dry_run": True, "note": "GOHUB_ENABLED=false; enable it on the VPS to hit TBS.",
                       "status_code": "0", "status_msg": "OK (dry-run)"}
            await self._audit_log(operation, body, request_xml, payload, None, started, dry_run=True)
            return payload

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{CTS_NAMESPACE}/{operation}"',
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
