"""Unit tests for the GoHub / CTS OnlineQR SOAP client.

Runs offline — no network calls. Uses `respx` to mock httpx and an in-memory
async db stub to verify audit-log writes.

Every test here maps to a specific spec constraint from
`BTS CTS-Operator Web Service Specification (OnlineQR) v1.2.11`.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from gohub_client import (  # noqa: E402
    GoHubClient, GoHubError, _envelope, _fmt_date_ddmmyyyy, _fmt_time_hhmmss,
    _md5_signature, _parse_response, _validate_and_normalize_seats,
    make_opetickno,
)


# ---------------------------------------------------------------------------
# Signature + envelope helpers (unchanged behaviour — regression checks)
# ---------------------------------------------------------------------------

def test_md5_signature_uses_my_local_date_and_matches_manual_hash():
    """Regression: TBS accepts md5(OTA + DD/MM/YYYY + PWD) as LOWERCASE hex.
    Verified live 2026-08 via ``scripts/gohub_sign_sweep.py``."""
    ota = "STARQISTINA"
    pw = "STCRF251C"
    my_tz = timezone(timedelta(hours=8))
    today = datetime.now(my_tz).strftime("%d/%m/%Y")
    expected = hashlib.md5(f"{ota}{today}{pw}".encode()).hexdigest()  # lowercase
    assert _md5_signature(ota, pw) == expected
    assert expected == expected.lower(), "signature must be lowercase hex"


def test_envelope_escapes_xml_special_chars():
    xml = _envelope("reserveOnlineQR_V2", {"TripNo": "SQ<001>&\"'A"}).decode()
    assert "&lt;001&gt;&amp;&quot;&apos;A" in xml
    assert 'xmlns="https://eticketing.tbsbts.com.my/ws_cts"' in xml


def test_envelope_skips_none_but_keeps_empty_string():
    xml = _envelope("op", {"a": "x", "b": None, "c": ""}).decode()
    assert "<a>x</a>" in xml
    assert "<b>" not in xml
    assert "<c></c>" in xml


# ---------------------------------------------------------------------------
# Spec §4: date + time formatting
# ---------------------------------------------------------------------------

def test_fmt_date_accepts_yyyymmdd_and_returns_ddmmyyyy():
    assert _fmt_date_ddmmyyyy("20260810") == "10/08/2026"


def test_fmt_date_accepts_hyphenated_iso():
    assert _fmt_date_ddmmyyyy("2026-08-10") == "10/08/2026"


def test_fmt_date_passes_through_ddmmyyyy():
    assert _fmt_date_ddmmyyyy("10/08/2026") == "10/08/2026"


def test_fmt_date_rejects_garbage():
    with pytest.raises(ValueError):
        _fmt_date_ddmmyyyy("Aug 10")


def test_fmt_time_hhmm_gets_seconds_appended():
    assert _fmt_time_hhmmss("2330") == "233000"


def test_fmt_time_passes_through_hhmmss():
    assert _fmt_time_hhmmss("233015") == "233015"


def test_fmt_time_strips_colons():
    assert _fmt_time_hhmmss("23:30") == "233000"
    assert _fmt_time_hhmmss("23:30:15") == "233015"


def test_fmt_time_rejects_garbage():
    with pytest.raises(ValueError):
        _fmt_time_hhmmss("11pm")


# ---------------------------------------------------------------------------
# Spec §4: make_opetickno constraints (max 20 chars, upper case)
# ---------------------------------------------------------------------------

def test_make_opetickno_composes_prefix_ref_seat():
    assert make_opetickno("BK123456", "1A") == "SQ-BK123456-1A"


def test_make_opetickno_uppercases():
    assert make_opetickno("bk123456", "1a") == "SQ-BK123456-1A"


def test_make_opetickno_rejects_missing_ref():
    with pytest.raises(ValueError):
        make_opetickno("", "1A")


def test_make_opetickno_rejects_when_over_20_chars():
    # 15 chars ref + 3 chars seat + "SQ--" = 22 chars -> should fail
    with pytest.raises(ValueError):
        make_opetickno("BOOKREF12345678", "12A")


# ---------------------------------------------------------------------------
# Seat validation — enforces CTS §4 mandatory fields.
# ---------------------------------------------------------------------------

def test_validate_seats_requires_opetickno():
    with pytest.raises(ValueError, match="opetickno"):
        _validate_and_normalize_seats([{"seatno": "1A", "name": "John"}])


def test_validate_seats_requires_name():
    with pytest.raises(ValueError, match="name"):
        _validate_and_normalize_seats([{"opetickno": "SQ-X-1A", "seatno": "1A"}])


def test_validate_seats_rejects_unknown_seattype():
    with pytest.raises(ValueError, match="seattype"):
        _validate_and_normalize_seats([{
            "opetickno": "SQ-X-1A", "name": "J", "seattype": "Z",
        }])


def test_validate_seats_normalizes_sprice_to_two_decimals():
    out = _validate_and_normalize_seats([{
        "opetickno": "SQ-X-1A", "name": "J", "sprice": 55,
    }])
    assert out[0]["sprice"] == "55.00"


def test_validate_seats_drops_empty_optional_fields():
    out = _validate_and_normalize_seats([{
        "opetickno": "SQ-X-1A", "name": "J", "ic": "", "contact": None,
    }])
    assert "ic" not in out[0]
    assert "contact" not in out[0]


def test_validate_seats_rejects_empty_list():
    with pytest.raises(ValueError, match="empty"):
        _validate_and_normalize_seats([])


# ---------------------------------------------------------------------------
# Response parsing — flat + nested + fault shapes.
# ---------------------------------------------------------------------------

def test_parse_response_success_returns_all_fields():
    xml = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reserveOnlineQR_V2Response xmlns="https://eticketing.tbsbts.com.my/ws_cts">
      <reserveOnlineQR_V2Result>
        <reserveOnlineQR_V2_status code="0" msg="OK">
          <reserveOnlineQR_V2_details>
            <ReservedID>R123</ReservedID>
            <QR>ABC-QR-STRING</QR>
          </reserveOnlineQR_V2_details>
        </reserveOnlineQR_V2_status>
      </reserveOnlineQR_V2Result>
    </reserveOnlineQR_V2Response>
  </soap:Body>
</soap:Envelope>"""
    out = _parse_response("reserveOnlineQR_V2", xml)
    assert out["status_code"] == "0"
    assert out["status_msg"] == "OK"
    assert out["ReservedID"] == "R123"
    assert out["QR"] == "ABC-QR-STRING"


def test_parse_response_non_ok_status_raises():
    xml = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reserveOnlineQR_V2Response xmlns="https://eticketing.tbsbts.com.my/ws_cts">
      <reserveOnlineQR_V2Result>
        <reserveOnlineQR_V2_status code="2" msg="Invalid signature">
          <reserveOnlineQR_V2_details />
        </reserveOnlineQR_V2_status>
      </reserveOnlineQR_V2Result>
    </reserveOnlineQR_V2Response>
  </soap:Body>
</soap:Envelope>"""
    with pytest.raises(GoHubError) as exc:
        _parse_response("reserveOnlineQR_V2", xml)
    assert exc.value.code == "2"
    assert "Invalid signature" in exc.value.message


def test_parse_response_invalid_ip_matches_live_shape():
    """Verified live 2026-02 against test endpoint — IP whitelist rejection."""
    xml = """<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><queryOnlineQRResponse xmlns="https://eticketing.tbsbts.com.my/ws_cts"><queryOnlineQRResult><queryOnlineQR_status code="2" msg="Invalid Server IP : |1.2.3.4|"><queryOnlineQR_details /></queryOnlineQR_status></queryOnlineQRResult></queryOnlineQRResponse></soap:Body></soap:Envelope>"""
    with pytest.raises(GoHubError) as exc:
        _parse_response("queryOnlineQR", xml)
    assert exc.value.code == "2"
    assert "Invalid Server IP" in exc.value.message


def test_parse_response_soap_fault_raises():
    xml = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <soap:Fault>
      <faultcode>Server</faultcode>
      <faultstring>Timeout</faultstring>
    </soap:Fault>
  </soap:Body>
</soap:Envelope>"""
    with pytest.raises(GoHubError) as exc:
        _parse_response("reserveOnlineQR_V2", xml)
    assert exc.value.code == "SOAP_FAULT"


# ---------------------------------------------------------------------------
# GoHubClient behaviour
# ---------------------------------------------------------------------------

class _FakeCollection:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return type("R", (), {"inserted_id": "fake_id"})()


class _FakeDB:
    def __init__(self):
        self.gohub_logs = _FakeCollection()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _sample_seats(booking_ref: str = "BK000001") -> list[dict]:
    return [
        {
            "opetickno": make_opetickno(booking_ref, "1A"),
            "seatno": "1A", "seattype": "A", "sprice": 55.0,
            "name": "Ali Bin Abu", "ic": "900101-14-5555", "contact": "0123456789",
        },
        {
            "opetickno": make_opetickno(booking_ref, "1B"),
            "seatno": "1B", "seattype": "C", "sprice": 27.5,
            "name": "Ali Jr",
        },
    ]


def test_client_dry_run_when_disabled(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "false")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "STARQISTINA")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "STCRF251C")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "QISTINA")
    db = _FakeDB()
    client = GoHubClient(db=db)
    out = _run(client.reserve_qr(
        trans_id="T1", trip_no="SQ001",
        trip_date="20260810", depart_date="20260810", depart_time="1130",
        from_counter="TBS01", to_counter="GMC01",
        seats=_sample_seats(),
    ))
    assert out["dry_run"] is True
    assert out["status_code"] == "0"
    assert len(db.gohub_logs.inserted) == 1
    assert db.gohub_logs.inserted[0]["dry_run"] is True


@respx.mock
def test_client_reserve_serialises_dates_and_time_correctly(monkeypatch):
    """Regression: date must go on the wire as DD/MM/YYYY, time as HHMMSS."""
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "STARQISTINA")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "STCRF251C")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "QISTINA")

    captured = {}
    def _capture(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content.decode()
        return httpx.Response(
            200,
            text='<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><reserveOnlineQR_V2Response xmlns="https://eticketing.tbsbts.com.my/ws_cts"><reserveOnlineQR_V2Result><reserveOnlineQR_V2_status code="0" msg="OK"><reserveOnlineQR_V2_details><reservedid>R1</reservedid></reserveOnlineQR_V2_details></reserveOnlineQR_V2_status></reserveOnlineQR_V2Result></reserveOnlineQR_V2Response></soap:Body></soap:Envelope>',
        )
    respx.post("http://example/test.asmx").mock(side_effect=_capture)

    _run(GoHubClient(db=_FakeDB()).reserve_qr(
        trans_id="T1", trip_no="SQ001",
        trip_date="20260810", depart_date="20260810", depart_time="1130",
        from_counter="TBS01", to_counter="GMC01",
        seats=_sample_seats(),
    ))

    body = captured["body"]
    # dates -> DD/MM/YYYY
    assert "<trip_date>10/08/2026</trip_date>" in body
    assert "<depart_date>10/08/2026</depart_date>" in body
    # time -> HHMMSS
    assert "<depart_time>113000</depart_time>" in body


@respx.mock
def test_client_reserve_emits_one_detail_per_seat(monkeypatch):
    """Multi-seat bookings must produce multiple <detail> elements in one call."""
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "STARQISTINA")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "STCRF251C")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "QISTINA")

    captured = {}
    def _capture(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content.decode()
        return httpx.Response(200, text='<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><reserveOnlineQR_V2Response xmlns="https://eticketing.tbsbts.com.my/ws_cts"><reserveOnlineQR_V2Result><reserveOnlineQR_V2_status code="0" msg="OK"><reserveOnlineQR_V2_details><reservedid>R1</reservedid></reserveOnlineQR_V2_details></reserveOnlineQR_V2_status></reserveOnlineQR_V2Result></reserveOnlineQR_V2Response></soap:Body></soap:Envelope>')
    respx.post("http://example/test.asmx").mock(side_effect=_capture)

    _run(GoHubClient(db=_FakeDB()).reserve_qr(
        trans_id="T1", trip_no="SQ001",
        trip_date="20260810", depart_date="20260810", depart_time="1130",
        from_counter="TBS01", to_counter="GMC01",
        seats=_sample_seats(),
    ))

    body = captured["body"]
    # two <detail /> elements, one <ticket_details> wrapper
    assert body.count("<detail ") == 2
    assert body.count("<ticket_details>") == 1
    # mandatory attrs present on each
    assert 'opetickno="SQ-BK000001-1A"' in body
    assert 'opetickno="SQ-BK000001-1B"' in body
    assert 'name="Ali Bin Abu"' in body
    assert 'name="Ali Jr"' in body
    # sprice formatted with 2 dp
    assert 'sprice="55.00"' in body
    assert 'sprice="27.50"' in body


@respx.mock
def test_client_get_qr_uses_getOnlineQR_V2_op(monkeypatch):
    """The one-shot flow must call getOnlineQR_V2, not reserve."""
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "STARQISTINA")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "STCRF251C")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "QISTINA")

    captured = {}
    def _capture(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content.decode()
        captured["soapaction"] = req.headers.get("SOAPAction", "")
        return httpx.Response(
            200,
            text='<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><getOnlineQR_V2Response xmlns="https://eticketing.tbsbts.com.my/ws_cts"><getOnlineQR_V2Result><getOnlineQR_V2_status code="0" msg="OK"><getOnlineQR_V2_details><detail opetickno="SQ-BK000001-1A" tickno="CTS0001" QR="OQR,QISTINA,cts001,ope001,hashhash"/></getOnlineQR_V2_details></getOnlineQR_V2_status></getOnlineQR_V2Result></getOnlineQR_V2Response></soap:Body></soap:Envelope>',
        )
    respx.post("http://example/test.asmx").mock(side_effect=_capture)

    out = _run(GoHubClient(db=_FakeDB()).get_qr(
        trans_id="T1", trip_no="SQ001",
        trip_date="20260810", depart_date="20260810", depart_time="1130",
        from_counter="TBS01", to_counter="GMC01",
        seats=_sample_seats()[:1],
    ))

    assert "<getOnlineQR_V2 " in captured["body"]
    assert "getOnlineQR_V2" in captured["soapaction"]
    assert out["status_code"] == "0"


@respx.mock
def test_client_confirm_requires_opetickno_per_seat(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "X"); monkeypatch.setenv("GOHUB_OTA_PASSWORD", "Y")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "Z")

    with pytest.raises(ValueError, match="opetickno"):
        _run(GoHubClient(db=_FakeDB()).confirm_qr(
            reserved_id="R1", seats=[{"newopetickno": "SQ-X-1A"}],
        ))


@respx.mock
def test_client_cancel_stacks_multiple_details(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "X"); monkeypatch.setenv("GOHUB_OTA_PASSWORD", "Y")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "Z")

    captured = {}
    def _capture(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content.decode()
        return httpx.Response(
            200,
            text='<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><cancelOnlineQRResponse xmlns="https://eticketing.tbsbts.com.my/ws_cts"><cancelOnlineQRResult><cancelOnlineQR_status code="0" msg="OK"><cancelOnlineQR_details /></cancelOnlineQR_status></cancelOnlineQRResult></cancelOnlineQRResponse></soap:Body></soap:Envelope>',
        )
    respx.post("http://example/test.asmx").mock(side_effect=_capture)

    _run(GoHubClient(db=_FakeDB()).cancel_qr(
        trans_id="T1", opeticknos=["SQ-BK000001-1A", "SQ-BK000001-1B"],
    ))

    body = captured["body"]
    assert body.count("<detail ") == 2
    assert 'opetickno="SQ-BK000001-1A"' in body
    assert 'opetickno="SQ-BK000001-1B"' in body


@respx.mock
def test_client_http_error_persisted_and_raised(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "X"); monkeypatch.setenv("GOHUB_OTA_PASSWORD", "Y")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "Z")
    respx.post("http://example/test.asmx").mock(return_value=httpx.Response(500, text="boom"))
    db = _FakeDB()
    client = GoHubClient(db=db)
    with pytest.raises(GoHubError) as exc:
        _run(client.confirm_qr(
            reserved_id="R1",
            seats=[{"opetickno": "SQ-BK000001-1A", "newopetickno": "SQ-BK000001-1A"}],
        ))
    assert exc.value.code == "HTTP_500"
    assert len(db.gohub_logs.inserted) == 1
    assert db.gohub_logs.inserted[0]["error"]["code"] == "HTTP_500"


def test_audit_log_never_persists_passwords(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "false")
    monkeypatch.setenv("GOHUB_BASE_URL", "")
    monkeypatch.setenv("GOHUB_OTA_CODE", "STARQISTINA")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "PLAINTEXT")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "QISTINA")
    db = _FakeDB()
    client = GoHubClient(db=db)
    _run(client.reserve_qr(
        trans_id="T", trip_no="SQ001",
        trip_date="20260810", depart_date="20260810", depart_time="1130",
        from_counter="TBS01", to_counter="GMC01",
        seats=_sample_seats(),
    ))
    log = db.gohub_logs.inserted[0]
    for k, v in log["request"].items():
        assert "PLAINTEXT" not in str(v), f"password leaked in key {k}"


def test_envelope_ticket_details_uses_attributes():
    """Verify the ticket_details block uses ATTRIBUTES, not child elements —
    the CTS WSDL is strict on this. Regression check for the schema fix."""
    xml = _envelope(
        "reserveOnlineQR_V2",
        {"ota_code": "X", "signature": "S", "trans_id": "T1"},
        ticket_details=[{"opetickno": "SQ-X-1A", "seatno": "1A", "seattype": "A",
                          "sprice": "55.00", "name": "John"}],
    ).decode()
    assert '<ticket_details><detail ' in xml
    assert 'seatno="1A"' in xml
    assert 'seattype="A"' in xml
    assert 'sprice="55.00"' in xml
    assert 'name="John"' in xml
    assert '<seatno>' not in xml
