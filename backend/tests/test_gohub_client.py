"""Unit tests for the GoHub / CTS OnlineQR SOAP client.

Runs offline — no network calls. Uses `respx` to mock httpx and an in-memory
async db stub to verify audit-log writes.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from gohub_client import (  # noqa: E402
    GoHubClient, GoHubError, _envelope, _md5_signature, _parse_response,
)


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------

def test_md5_signature_uses_my_local_date_and_matches_manual_hash():
    ota = "STARQISTINA"
    pw = "STCRF251C"
    my_tz = timezone(timedelta(hours=8))
    today = datetime.now(my_tz).strftime("%Y%m%d")
    expected = hashlib.md5(f"{ota}{today}{pw}".encode()).hexdigest().upper()
    assert _md5_signature(ota, pw) == expected


def test_envelope_escapes_xml_special_chars():
    xml = _envelope("reserveOnlineQR_V2", {"TripNo": "SQ<001>&\"'A"}).decode()
    assert "&lt;001&gt;&amp;&quot;&apos;A" in xml
    assert 'xmlns="http://tempuri.org/"' in xml


def test_envelope_skips_none_but_keeps_empty_string():
    xml = _envelope("op", {"a": "x", "b": None, "c": ""}).decode()
    assert "<a>x</a>" in xml
    assert "<b>" not in xml  # skipped
    assert "<c></c>" in xml   # explicit empty kept


def test_parse_response_success_returns_all_fields():
    xml = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reserveOnlineQR_V2Response xmlns="http://tempuri.org/">
      <reserveOnlineQR_V2Result>
        <StatusCode>00</StatusCode>
        <StatusDescription>OK</StatusDescription>
        <ReservedID>R123</ReservedID>
        <QR>ABC-QR-STRING</QR>
      </reserveOnlineQR_V2Result>
    </reserveOnlineQR_V2Response>
  </soap:Body>
</soap:Envelope>"""
    out = _parse_response("reserveOnlineQR_V2", xml)
    assert out["StatusCode"] == "00"
    assert out["ReservedID"] == "R123"
    assert out["QR"] == "ABC-QR-STRING"


def test_parse_response_non_ok_status_raises():
    xml = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reserveOnlineQR_V2Response xmlns="http://tempuri.org/">
      <reserveOnlineQR_V2Result>
        <StatusCode>99</StatusCode>
        <StatusDescription>Invalid signature</StatusDescription>
      </reserveOnlineQR_V2Result>
    </reserveOnlineQR_V2Response>
  </soap:Body>
</soap:Envelope>"""
    with pytest.raises(GoHubError) as exc:
        _parse_response("reserveOnlineQR_V2", xml)
    assert exc.value.code == "99"
    assert "Invalid signature" in exc.value.message


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


def test_client_dry_run_when_disabled(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "false")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "STARQISTINA")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "STCRF251C")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "QISTINA")
    db = _FakeDB()
    client = GoHubClient(db=db)
    out = _run(client.reserve_qr(
        trans_id="T1", trip_no="SQ001", boarding_date="20260810",
        boarding_time="1130", seat_number="1A",
        from_counter="TBS01", to_counter="GMC01",
    ))
    assert out["dry_run"] is True
    assert out["StatusCode"] == "00"
    assert len(db.gohub_logs.inserted) == 1
    assert db.gohub_logs.inserted[0]["dry_run"] is True


@respx.mock
def test_client_live_call_success(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "STARQISTINA")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "STCRF251C")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "QISTINA")
    mocked_response = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reserveOnlineQR_V2Response xmlns="http://tempuri.org/">
      <reserveOnlineQR_V2Result>
        <StatusCode>00</StatusCode>
        <StatusDescription>OK</StatusDescription>
        <ReservedID>R123</ReservedID>
        <QR>TESTQR001</QR>
      </reserveOnlineQR_V2Result>
    </reserveOnlineQR_V2Response>
  </soap:Body>
</soap:Envelope>"""
    respx.post("http://example/test.asmx").mock(
        return_value=httpx.Response(200, text=mocked_response)
    )
    db = _FakeDB()
    client = GoHubClient(db=db)
    out = _run(client.reserve_qr(
        trans_id="T1", trip_no="SQ001", boarding_date="20260810",
        boarding_time="1130", seat_number="1A",
        from_counter="TBS01", to_counter="GMC01",
    ))
    assert out["QR"] == "TESTQR001"
    assert out["ReservedID"] == "R123"
    assert len(db.gohub_logs.inserted) == 1
    log = db.gohub_logs.inserted[0]
    assert log["operation"] == "reserveOnlineQR_V2"
    assert log["dry_run"] is False
    assert log["error"] is None


@respx.mock
def test_client_http_error_persisted_and_raised(monkeypatch):
    monkeypatch.setenv("GOHUB_ENABLED", "true")
    monkeypatch.setenv("GOHUB_BASE_URL", "http://example/test.asmx")
    monkeypatch.setenv("GOHUB_OTA_CODE", "X")
    monkeypatch.setenv("GOHUB_OTA_PASSWORD", "Y")
    monkeypatch.setenv("GOHUB_OPERATOR_CODE", "Z")
    respx.post("http://example/test.asmx").mock(return_value=httpx.Response(500, text="boom"))
    db = _FakeDB()
    client = GoHubClient(db=db)
    with pytest.raises(GoHubError) as exc:
        _run(client.confirm_qr(trans_id="T1", reserved_id="R1"))
    assert exc.value.code == "HTTP_500"
    # Audit log MUST still capture the failed attempt for post-mortem.
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
        trans_id="T", trip_no="SQ001", boarding_date="20260810", boarding_time="1130",
        seat_number="1A", from_counter="TBS01", to_counter="GMC01",
    ))
    log = db.gohub_logs.inserted[0]
    # No field literally named *password*/PLAINTEXT should leak into the log.
    for k, v in log["request"].items():
        assert "PLAINTEXT" not in str(v), f"password leaked in key {k}"
