"""Tests for the new city-level /api/search feature + stop-level regression."""
import os
import pytest
import requests
from datetime import datetime, timedelta, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://star-qistna-bus.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def terminals():
    r = requests.get(f"{API}/terminals", timeout=15)
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def kl_and_sg_terminals(terminals):
    all_t = terminals["all"]
    kl = [t for t in all_t if t["city"] == "Kuala Lumpur"]
    sg = [t for t in all_t if t["city"] == "Singapore"]
    assert kl and sg, "Seed data missing KL / SG terminals"
    return kl, sg


def _future_date(days=2):
    # Use MY/SG local time (UTC+8) — matches backend
    now_local = datetime.now(timezone(timedelta(hours=8)))
    return (now_local + timedelta(days=days)).date().isoformat()


# ---- Regression: stop-level search still works ----
class TestStopLevelRegression:
    def test_stop_level_search_shape(self, kl_and_sg_terminals):
        kl, sg = kl_and_sg_terminals
        date = _future_date(3)
        r = requests.get(
            f"{API}/search",
            params={"from_terminal_id": kl[0]["id"], "to_terminal_id": sg[0]["id"], "date": date},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Shape check
        assert set(["from", "to", "date", "schedules"]).issubset(data.keys())
        assert data["date"] == date
        # from/to are actual terminal docs, not city-marker docs
        assert data["from"].get("id") == kl[0]["id"]
        assert data["to"].get("id") == sg[0]["id"]
        assert data["from"].get("is_city") is not True
        assert data["to"].get("is_city") is not True
        assert isinstance(data["schedules"], list)


# ---- City-level search ----
class TestCityLevelSearch:
    def test_city_to_city_search(self):
        date = _future_date(3)
        r = requests.get(
            f"{API}/search",
            params={"from_city": "Kuala Lumpur", "to_city": "Singapore", "date": date},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # from meta
        assert data["from"].get("is_city") is True
        assert data["from"].get("city") == "Kuala Lumpur"
        assert isinstance(data["from"].get("stop_count"), int)
        assert data["from"]["stop_count"] >= 1
        # to meta
        assert data["to"].get("is_city") is True
        assert data["to"].get("city") == "Singapore"
        assert isinstance(data["to"].get("stop_count"), int)
        assert data["to"]["stop_count"] >= 1
        # schedules aggregate across terminals — should be >= what one stop returns
        assert isinstance(data["schedules"], list)

    def test_city_to_city_aggregates_more_than_single_stop(self, kl_and_sg_terminals):
        """City-level should return >= number of schedules of any single pair."""
        kl, sg = kl_and_sg_terminals
        date = _future_date(3)
        city_r = requests.get(f"{API}/search", params={"from_city": "Kuala Lumpur", "to_city": "Singapore", "date": date}, timeout=15)
        stop_r = requests.get(f"{API}/search", params={"from_terminal_id": kl[0]["id"], "to_terminal_id": sg[0]["id"], "date": date}, timeout=15)
        assert city_r.status_code == 200 and stop_r.status_code == 200
        assert len(city_r.json()["schedules"]) >= len(stop_r.json()["schedules"])

    def test_mixed_mode_terminal_from_city_to(self, kl_and_sg_terminals):
        kl, _ = kl_and_sg_terminals
        date = _future_date(3)
        r = requests.get(
            f"{API}/search",
            params={"from_terminal_id": kl[0]["id"], "to_city": "Singapore", "date": date},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # from is specific stop, to is city
        assert data["from"].get("id") == kl[0]["id"]
        assert data["from"].get("is_city") is not True
        assert data["to"].get("is_city") is True
        assert data["to"].get("city") == "Singapore"


# ---- Error cases ----
class TestSearchErrors:
    def test_no_params_returns_400(self):
        date = _future_date(3)
        r = requests.get(f"{API}/search", params={"date": date}, timeout=15)
        assert r.status_code == 400
        detail = r.json().get("detail", "")
        assert "from_terminal_id" in detail and "from_city" in detail

    def test_bogus_city_returns_400(self):
        date = _future_date(3)
        r = requests.get(
            f"{API}/search",
            params={"from_city": "Atlantis-No-Such-City", "to_city": "Singapore", "date": date},
            timeout=15,
        )
        assert r.status_code == 400

    def test_missing_to_returns_400(self, kl_and_sg_terminals):
        kl, _ = kl_and_sg_terminals
        date = _future_date(3)
        r = requests.get(f"{API}/search", params={"from_terminal_id": kl[0]["id"], "date": date}, timeout=15)
        assert r.status_code == 400
