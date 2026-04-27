"""One-off helper: seed a confirmed booking for a fresh test user, return id+token+ref."""
import os, sys, uuid, json, requests
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://star-qistna-bus.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

MY_TZ = timezone(timedelta(hours=8))


def main(scenario="full_refund"):
    """scenario: 'full_refund' (≥24h, no paid txn so won't try Stripe but we set total=0 to force burn-with-no-stripe-call?
    Actually we want UI to show 'Full refund' headline. quote endpoint is independent of Stripe.
    For UI test, we need confirmed booking ≥24h with total>0 → quote shows refund_eligible=true."""
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    email = f"TEST_uicancel_{uuid.uuid4().hex[:8]}@test.com"
    pwd = "Pass@1234"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": pwd, "full_name": "UI Cancel Test"})
    assert r.status_code in (200, 201), r.text
    token = r.json()["access_token"]
    me = requests.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    uid = me["id"]

    terms = list(db.terminals.find({}, {"_id": 0}).limit(2))
    sched = db.schedules.find_one({}, {"_id": 0})
    bid = str(uuid.uuid4())
    ref = f"UIT{uuid.uuid4().hex[:8].upper()}"
    dep = datetime.now(MY_TZ).replace(tzinfo=None) + timedelta(days=3)

    db.bookings.insert_one({
        "id": bid, "reference": ref, "user_id": uid, "status": "confirmed",
        "from_terminal_id": terms[0]["id"], "to_terminal_id": terms[1]["id"],
        "schedule_id": sched["id"],
        "departure_date": dep.strftime("%Y-%m-%d"), "departure_time": dep.strftime("%H:%M"),
        "seats": ["A1"],
        "passengers": [{"name": "UI Pax", "category": "adult", "seat_number": "A1", "ic_or_passport": ""}],
        "pricing": {"currency": "myr", "adults": 1, "children": 0,
                    "adult_fare": 75.0, "child_fare": 0, "discount": 0, "total": 75.0},
        "contact_email": email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.seat_locks.insert_one({
        "id": str(uuid.uuid4()), "booking_id": bid, "schedule_id": sched["id"],
        "departure_date": dep.strftime("%Y-%m-%d"), "seat_number": "A1",
        "status": "booked", "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Also seed a < 24h confirmed booking for the burn UI test
    bid2 = str(uuid.uuid4())
    ref2 = f"UIB{uuid.uuid4().hex[:8].upper()}"
    dep2 = datetime.now(MY_TZ).replace(tzinfo=None) + timedelta(hours=2)
    db.bookings.insert_one({
        "id": bid2, "reference": ref2, "user_id": uid, "status": "confirmed",
        "from_terminal_id": terms[0]["id"], "to_terminal_id": terms[1]["id"],
        "schedule_id": sched["id"],
        "departure_date": dep2.strftime("%Y-%m-%d"), "departure_time": dep2.strftime("%H:%M"),
        "seats": ["A2"],
        "passengers": [{"name": "UI Pax2", "category": "adult", "seat_number": "A2", "ic_or_passport": ""}],
        "pricing": {"currency": "myr", "adults": 1, "children": 0,
                    "adult_fare": 60.0, "child_fare": 0, "discount": 0, "total": 60.0},
        "contact_email": email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    print(json.dumps({"email": email, "password": pwd, "token": token,
                      "refund_booking_id": bid, "refund_ref": ref,
                      "burn_booking_id": bid2, "burn_ref": ref2}))


if __name__ == "__main__":
    main()
