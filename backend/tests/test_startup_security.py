"""Unit tests for fail-fast startup security validation."""
from __future__ import annotations

import os
import sys
import asyncio
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "starqistna_startup_test")
os.environ.setdefault("JWT_SECRET", "startup-test-secret")
os.environ.setdefault("STRIPE_API_KEY", "sk_test_startup")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "startup-test-password")

import server  # noqa: E402


@pytest.fixture
def valid_production_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://booking.test")
    monkeypatch.delenv("SEED_DEMO_DATA", raising=False)
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", "whsec_startup_test")
    monkeypatch.setattr(server, "JWT_SECRET", "a-real-test-jwt-secret")
    monkeypatch.setattr(server, "BOARDING_API_KEY", "a-real-test-boarding-key")
    monkeypatch.setattr(server, "BOOTSTRAP_ADMIN_PASSWORD", "a-real-test-admin-password")
    monkeypatch.setattr(server, "STRIPE_API_KEY", "sk_live_startup_test")


def test_valid_production_configuration_starts(valid_production_config):
    server._validate_startup_security_config()


def test_startup_fails_before_database_initialization(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://booking.test")
    monkeypatch.setattr(server, "BOARDING_API_KEY", "")

    async def unexpected_initialization():
        pytest.fail("database initialization must not run with unsafe configuration")

    monkeypatch.setattr(server, "_seed_terminals", unexpected_initialization)

    async def start():
        with pytest.raises(RuntimeError, match="BOARDING_API_KEY"):
            await server.on_start()

    asyncio.run(start())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("BOARDING_API_KEY", ""),
        ("BOARDING_API_KEY", "REPLACE_ME"),
        ("STRIPE_WEBHOOK_SECRET", ""),
        ("STRIPE_WEBHOOK_SECRET", "whsec_REPLACE_ME"),
        ("JWT_SECRET", "REPLACE_WITH_SECRET"),
        ("BOOTSTRAP_ADMIN_PASSWORD", "REPLACE_ME_PASSWORD"),
        ("STRIPE_API_KEY", "sk_test_not_live"),
    ],
)
def test_unsafe_production_configuration_fails_without_leaking_values(
    valid_production_config, monkeypatch, field, value
):
    monkeypatch.setattr(server, field, value)
    with pytest.raises(RuntimeError) as error:
        server._validate_startup_security_config()
    assert field in str(error.value)
    assert value not in str(error.value)


@pytest.mark.parametrize("app_env", ["", "prodution", "staging"])
def test_app_env_must_be_explicit_and_recognized(monkeypatch, app_env):
    monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(RuntimeError, match="APP_ENV"):
        server._validate_startup_security_config()


def test_nonproduction_allows_local_placeholder_configuration(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("SEED_DEMO_DATA", "true")
    server._validate_startup_security_config()


@pytest.mark.parametrize(
    ("environment", "value", "message"),
    [
        ("CORS_ORIGINS", "*", "CORS_ORIGINS"),
        ("SEED_DEMO_DATA", "true", "SEED_DEMO_DATA"),
    ],
)
def test_unsafe_production_runtime_options_fail(
    valid_production_config, monkeypatch, environment, value, message
):
    monkeypatch.setenv(environment, value)
    with pytest.raises(RuntimeError, match=message):
        server._validate_startup_security_config()
