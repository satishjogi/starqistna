"""
Transit E1 - Bus Booking System Backend
FastAPI + MongoDB. API-first so future mobile (React Native) uses same endpoints.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError
from core.config import BOOTSTRAP_ADMIN_PASSWORD, client, db, logger
from core.utils import _layout_for_bus_type, new_id, utcnow
from core.security import hash_password
from routers import feedback as feedback_router
from routers import promo as promo_router
from routers import terminals as terminals_router
from routers import admin_users as admin_users_router
from routers import auth as auth_router
from routers import bookings as bookings_router
from routers import bus_types as bus_types_router
from routers import gohub as gohub_router
from routers import payments as payments_router
from routers import routes as routes_router
from routers import schedules as schedules_router
from routers import search as search_router
from routers import seats as seats_router
from routers import settings as settings_router
from routers.auth import LOGIN_WINDOW_SECONDS
from routers.payments import _reconcile_loop

app = FastAPI(title="Transit E1 - Bus Booking API")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ---------- Seeding & Indexes ----------
MALAYSIAN_TERMINALS = [
    # (city, name, code, state, country)
    ("Kuala Lumpur", "KL Sentral", "KLS", "WP", "MY"),
    ("Kuala Lumpur", "Terminal Bersepadu Selatan (TBS)", "TBS", "WP", "MY"),
    ("Kuala Lumpur", "Pudu Sentral", "PDS", "WP", "MY"),
    ("Kuala Lumpur", "Hentian Duta", "HDT", "WP", "MY"),
    ("Penang", "Sungai Nibong", "SGN", "PG", "MY"),
    ("Penang", "Butterworth Penang Sentral", "BPS", "PG", "MY"),
    ("Johor Bahru", "Larkin Sentral", "LKS", "JH", "MY"),
    ("Johor Bahru", "JB Sentral (Causeway)", "JBS", "JH", "MY"),
    ("Melaka", "Melaka Sentral", "MKS", "ML", "MY"),
    ("Ipoh", "Ipoh Amanjaya", "IPA", "PK", "MY"),
    ("Singapore", "Golden Mile Complex", "GMC", "SG", "SG"),
    ("Singapore", "Queen Street Terminal", "QST", "SG", "SG"),
    ("Kota Bharu", "Kota Bharu Terminal", "KTB", "KN", "MY"),
    ("Kuantan", "Terminal Sentral Kuantan", "TSK", "PH", "MY"),
]

COUNTRY_TO_CURRENCY = {"MY": "myr", "SG": "sgd"}


async def _seed_terminals():
    if await db.terminals.count_documents({}) > 0:
        # Backfill country on any existing record missing it
        async for t in db.terminals.find({"country": {"$exists": False}}):
            country = "SG" if t.get("city") == "Singapore" else "MY"
            await db.terminals.update_one({"id": t["id"]}, {"$set": {"country": country}})
        return
    for city, name, code, state, country in MALAYSIAN_TERMINALS:
        await db.terminals.insert_one(
            {"id": new_id(), "city": city, "name": name, "code": code, "state": state, "country": country}
        )
    logger.info("Seeded %d terminals", len(MALAYSIAN_TERMINALS))


OPERATORS = ["Qistna Express"]
BUS_TYPES = ["VIP 27", "Executive", "Standard"]


async def _seed_schedules():
    if await db.schedules.count_documents({}) > 0:
        return
    terms = await db.terminals.find({}, {"_id": 0}).to_list(500)
    # Popular routes
    popular_routes = [
        ("Kuala Lumpur", "Penang"),
        ("Kuala Lumpur", "Johor Bahru"),
        ("Kuala Lumpur", "Melaka"),
        ("Kuala Lumpur", "Ipoh"),
        ("Kuala Lumpur", "Singapore"),
        ("Penang", "Kuala Lumpur"),
        ("Johor Bahru", "Kuala Lumpur"),
        ("Singapore", "Kuala Lumpur"),
        ("Melaka", "Singapore"),
        ("Kuala Lumpur", "Kuantan"),
    ]

    def pick_main(city):
        for t in terms:
            if t["city"] == city:
                return t
        return None

    today = datetime.now(timezone.utc).date()
    total = 0
    for dep_city, arr_city in popular_routes:
        from_t = pick_main(dep_city)
        to_t = pick_main(arr_city)
        if not from_t or not to_t:
            continue
        # also generate second terminal option for KL
        alt_from = None
        if dep_city == "Kuala Lumpur":
            for t in terms:
                if t["city"] == "Kuala Lumpur" and t["id"] != from_t["id"]:
                    alt_from = t
                    break

        for day_offset in range(0, 14):
            dep_date = (today + timedelta(days=day_offset)).isoformat()
            times = [("08:00", "12:30", 55.0), ("11:30", "16:00", 49.0), ("14:00", "18:30", 52.0), ("22:00", "02:30", 65.0)]
            for i, (dt, at, fare) in enumerate(times):
                op = OPERATORS[i % len(OPERATORS)]
                bt = BUS_TYPES[i % len(BUS_TYPES)]
                origin_term = alt_from if (i == 1 and alt_from) else from_t
                sched_currency = COUNTRY_TO_CURRENCY.get(origin_term.get("country", "MY"), "myr")
                # For SGD schedules, approximate local pricing (MYR * 0.33 rounded to nearest whole)
                sched_fare = round(fare * 0.33) if sched_currency == "sgd" else fare
                await db.schedules.insert_one(
                    {
                        "id": new_id(),
                        "from_terminal_id": origin_term["id"],
                        "to_terminal_id": to_t["id"],
                        "departure_date": dep_date,
                        "departure_time": dt,
                        "arrival_time": at,
                        "bus_operator": op,
                        "bus_type": bt,
                        "adult_fare": sched_fare,
                        "rows": 10,
                        "total_seats": 40,
                        "currency": sched_currency,
                        "created_at": utcnow().isoformat(),
                    }
                )
                total += 1
    logger.info("Seeded %d schedules", total)


async def _migrate_operator_names():
    """All schedules belong to single operator 'Qistna Express'."""
    result = await db.schedules.update_many(
        {"bus_operator": {"$ne": "Qistna Express"}},
        {"$set": {"bus_operator": "Qistna Express"}},
    )
    if result.modified_count:
        logger.info("Migrated %d schedules to Qistna Express operator", result.modified_count)


async def _migrate_bus_instance_ids():
    """Backfill `bus_instance_id` on schedules + seat_locks.

    Rule: schedules that share (route_id, departure_date, departure_time) are the
    same physical bus and MUST share a seat pool. Admin can create multiple
    schedule rows per physical bus (one per pickup/drop-off segment) — they all
    resolve to the same bus_instance_id so a seat booked on any of them blocks
    the seat on all siblings.

    Legacy (unlinked) schedules use their own `id` as bus_instance_id — no
    behaviour change, just makes the index key uniform across the collection.
    """
    updated_scheds = 0
    # 1. Legacy / unlinked schedules → bus_instance_id = id
    async for s in db.schedules.find(
        {"$or": [{"route_id": None}, {"route_id": {"$exists": False}}],
         "bus_instance_id": {"$exists": False}},
        {"_id": 0, "id": 1},
    ):
        await db.schedules.update_one({"id": s["id"]}, {"$set": {"bus_instance_id": s["id"]}})
        updated_scheds += 1
    # 2. Route-linked schedules → group by (route_id, date, time), share one id
    pipeline = [
        {"$match": {"route_id": {"$ne": None, "$exists": True},
                    "bus_instance_id": {"$exists": False}}},
        {"$group": {"_id": {"r": "$route_id", "d": "$departure_date", "t": "$departure_time"},
                    "ids": {"$push": "$id"}}},
    ]
    async for g in db.schedules.aggregate(pipeline):
        instance_id = new_id()
        await db.schedules.update_many(
            {"id": {"$in": g["ids"]}},
            {"$set": {"bus_instance_id": instance_id}},
        )
        updated_scheds += len(g["ids"])
    if updated_scheds:
        logger.info("Migrated %d schedules to bus_instance_id keys", updated_scheds)

    # 3. Backfill seat_locks: bus_instance_id from parent schedule.
    #    We do this in one aggregation-style batch to avoid N round-trips.
    missing = await db.seat_locks.count_documents({"bus_instance_id": {"$exists": False}})
    if missing:
        # Load a schedule_id → bus_instance_id map (small; 5k rows).
        sched_map: Dict[str, str] = {}
        async for s in db.schedules.find({}, {"_id": 0, "id": 1, "bus_instance_id": 1}):
            sched_map[s["id"]] = s.get("bus_instance_id") or s["id"]
        updated_locks = 0
        async for lock in db.seat_locks.find(
            {"bus_instance_id": {"$exists": False}}, {"_id": 0, "schedule_id": 1},
        ):
            bi = sched_map.get(lock["schedule_id"], lock["schedule_id"])
            await db.seat_locks.update_many(
                {"schedule_id": lock["schedule_id"], "bus_instance_id": {"$exists": False}},
                {"$set": {"bus_instance_id": bi}},
            )
            updated_locks += 1
            if updated_locks >= missing:
                break
        logger.info("Backfilled bus_instance_id on %d seat_locks", missing)


async def _diversify_popular_schedules():
    """For each popular city pair, ensure varied departure times over the next 14 days
    so the 'Popular right now' section shows genuinely different countdowns per route
    (not all landing on the same identical seed slot).

    Safe & idempotent: only adds NEW schedule rows where none exist for that specific
    (from, to, date, time) tuple. Existing schedules & bookings are never touched.
    """
    # Route-specific varied departure time sets — each pair gets its own mix of morning,
    # afternoon, evening, and late-night slots. Times are (dep, arr, fare_myr).
    ROUTE_TIMES: dict = {
        ("Kuala Lumpur", "Melaka"):      [("07:15", "09:45", 25), ("10:30", "13:00", 25), ("13:45", "16:15", 25), ("17:20", "19:50", 28), ("19:00", "21:30", 25)],
        ("Kuala Lumpur", "Johor Bahru"): [("06:45", "11:15", 45), ("09:30", "14:00", 45), ("12:15", "16:45", 45), ("15:40", "20:10", 49), ("19:45", "00:15", 52)],
        ("Kuala Lumpur", "Penang"):      [("07:00", "11:30", 49), ("10:15", "14:45", 49), ("13:30", "18:00", 49), ("17:50", "22:20", 52), ("21:15", "01:45", 55)],
        ("Kuala Lumpur", "Singapore"):   [("07:30", "13:00", 55), ("11:00", "16:30", 55), ("15:15", "20:45", 58), ("22:30", "04:00", 65)],
        ("Kuala Lumpur", "Ipoh"):        [("08:20", "10:45", 35), ("12:10", "14:35", 35), ("16:05", "18:30", 35), ("20:40", "23:05", 38)],
        ("Kuala Lumpur", "Kuantan"):     [("08:15", "12:15", 42), ("13:25", "17:25", 42), ("18:35", "22:35", 45)],
        ("Penang", "Johor Bahru"):       [("06:30", "13:30", 75), ("10:00", "17:00", 75), ("20:00", "03:00", 80)],
        ("Penang", "Kuala Lumpur"):      [("07:45", "12:15", 49), ("11:20", "15:50", 49), ("14:55", "19:25", 49), ("18:30", "23:00", 52)],
        ("Singapore", "Kuala Lumpur"):   [("07:00", "12:30", 55), ("11:45", "17:15", 55), ("16:20", "21:50", 58), ("22:00", "03:30", 65)],
        ("Johor Bahru", "Kuala Lumpur"): [("06:50", "11:20", 45), ("10:05", "14:35", 45), ("13:50", "18:20", 45), ("17:25", "21:55", 49), ("20:30", "01:00", 52)],
        ("Melaka", "Singapore"):         [("07:50", "11:50", 55), ("12:30", "16:30", 55), ("18:10", "22:10", 58)],
    }

    # Pre-index terminals by city -> first matching terminal
    all_terms = await db.terminals.find({}, {"_id": 0}).to_list(500)
    city_to_term: dict = {}
    for t in all_terms:
        city_to_term.setdefault(t["city"], t)

    today = datetime.now(timezone.utc).date()
    added = 0
    for (from_city, to_city), times in ROUTE_TIMES.items():
        f = city_to_term.get(from_city)
        t = city_to_term.get(to_city)
        if not f or not t:
            continue
        # Derive currency from origin country (same rule as seed)
        currency = COUNTRY_TO_CURRENCY.get(f.get("country"), "myr")
        for day_offset in range(0, 14):
            dep_date = (today + timedelta(days=day_offset)).isoformat()
            for dep_time, arr_time, fare in times:
                fare_val = fare if currency == "myr" else round(fare * 0.33)
                exists = await db.schedules.find_one(
                    {
                        "from_terminal_id": f["id"],
                        "to_terminal_id": t["id"],
                        "departure_date": dep_date,
                        "departure_time": dep_time,
                    },
                    {"_id": 0, "id": 1},
                )
                if exists:
                    continue
                await db.schedules.insert_one(
                    {
                        "id": new_id(),
                        "from_terminal_id": f["id"],
                        "to_terminal_id": t["id"],
                        "departure_date": dep_date,
                        "departure_time": dep_time,
                        "arrival_time": arr_time,
                        "bus_operator": "Qistna Express",
                        "bus_type": "Executive",
                        "adult_fare": fare_val,
                        "rows": 10,
                        "total_seats": 40,
                        "currency": currency,
                        "created_at": utcnow().isoformat(),
                    }
                )
                added += 1
    if added:
        logger.info("Diversified popular-route schedules: +%d rows", added)


async def _migrate_sg_schedules():
    """One-time migration: for schedules where the origin terminal is in Singapore,
    ensure currency is 'sgd' and fares are adjusted from MYR."""
    sg_terminals = await db.terminals.find({"country": "SG"}, {"_id": 0, "id": 1}).to_list(100)
    sg_ids = [t["id"] for t in sg_terminals]
    if not sg_ids:
        return
    wrong = db.schedules.find({"from_terminal_id": {"$in": sg_ids}, "currency": {"$ne": "sgd"}}, {"_id": 0})
    fixed = 0
    async for s in wrong:
        new_fare = round(float(s.get("adult_fare", 0)) * 0.33)
        await db.schedules.update_one(
            {"id": s["id"]},
            {"$set": {"currency": "sgd", "adult_fare": new_fare}},
        )
        fixed += 1
    if fixed:
        logger.info("Migrated %d SG-origin schedules to SGD pricing", fixed)


async def _seed_admin():
    """Idempotent bootstrap admin seed.

    On every startup:
      1. If no admin exists → seed it with BOOTSTRAP_ADMIN_PASSWORD.
      2. If admin exists AND its current password_hash still verifies against
         BOOTSTRAP_ADMIN_PASSWORD → no-op (steady state).
      3. If admin exists BUT the env password no longer matches the stored
         hash → this usually happens after `.env` changes on production.
         By default we log a big WARNING with recovery instructions.
         If `RESET_ADMIN_ON_BOOT=true` is set in env, we force-resync the
         password_hash to the current env value. This lets operators recover
         a locked-out admin with a single .env flip + restart.
    """
    existing = await db.users.find_one({"email": "admin@starqistna.com"})
    reset_flag = os.environ.get("RESET_ADMIN_ON_BOOT", "").strip().lower() in ("1", "true", "yes")

    if existing:
        updates: dict = {}
        unsets: dict = {}
        if existing.get("role") != "super_admin":
            updates["role"] = "super_admin"
        if existing.get("is_active") is None:
            updates["is_active"] = True
        if not existing.get("is_admin"):
            updates["is_admin"] = True
        if existing.get("full_name") == "Star Qistna Admin":
            updates["full_name"] = "Qistna Express Admin"

        stored_hash = existing.get("password_hash") or ""
        env_matches_stored = bool(stored_hash) and verify_password(BOOTSTRAP_ADMIN_PASSWORD, stored_hash)

        if not env_matches_stored:
            if reset_flag:
                updates["password_hash"] = hash_password(BOOTSTRAP_ADMIN_PASSWORD)
                unsets["must_change_password"] = ""
                logger.warning(
                    "RESET_ADMIN_ON_BOOT=true → admin password re-synced to BOOTSTRAP_ADMIN_PASSWORD. "
                    "IMPORTANT: remove RESET_ADMIN_ON_BOOT from .env and restart so this doesn't run again."
                )
            else:
                logger.warning(
                    "\n" + ("=" * 70) + "\n"
                    "  Admin exists but BOOTSTRAP_ADMIN_PASSWORD in .env does NOT match\n"
                    "  the password hash stored in MongoDB. Login with the env value\n"
                    "  will fail.\n\n"
                    "  To recover, choose ONE:\n"
                    "    A) Set RESET_ADMIN_ON_BOOT=true in backend/.env, restart the\n"
                    "       backend, then remove the flag and restart again.\n"
                    "    B) Run: python scripts/reset_admin_password.py\n"
                    + ("=" * 70)
                )

        if updates or unsets:
            op: dict = {}
            if updates:
                op["$set"] = updates
            if unsets:
                op["$unset"] = unsets
            await db.users.update_one({"id": existing["id"]}, op)
        return

    # First-boot seed.
    await db.users.insert_one(
        {
            "id": new_id(),
            "email": "admin@starqistna.com",
            "full_name": "Qistna Express Admin",
            "phone": "+60123456789",
            "password_hash": hash_password(BOOTSTRAP_ADMIN_PASSWORD),
            "is_admin": True,
            "role": "super_admin",
            "is_active": True,
            "must_change_password": True,
            "created_at": utcnow().isoformat(),
        }
    )
    logger.info("Seeded bootstrap super-admin (email: admin@starqistna.com). Change password on first login.")


async def _seed_promos():
    if await db.promo_codes.count_documents({}) > 0:
        return
    samples = [
        {"code": "WELCOME10", "type": "percent", "value": 10, "currency": "myr",
         "max_uses": 1000, "valid_until": None, "active": True,
         "description": "10% off any booking"},
        {"code": "RAYA5", "type": "flat", "value": 5, "currency": "myr",
         "max_uses": None, "valid_until": None, "active": True,
         "description": "RM 5 off any booking"},
    ]
    for s in samples:
        await db.promo_codes.insert_one(
            {**s, "id": new_id(), "used_count": 0, "created_at": utcnow().isoformat()}
        )
    logger.info("Seeded %d promo codes", len(samples))


async def _seed_bus_types():
    """Seed the three default bus classes we've been using since day one.
    Idempotent — skips any name that already exists (admin may have edited seat counts)."""
    defaults = [
        {"name": "VIP 27", "seat_count": 27,
         "description": "Luxury 2+1 layout with massage seats", "image_url": None},
        {"name": "Executive", "seat_count": 40,
         "description": "Reclining 2+2 seats · premium comfort", "image_url": None},
        {"name": "Standard", "seat_count": 40,
         "description": "Standard 2+2 layout", "image_url": None},
    ]
    inserted = 0
    for bt in defaults:
        if await db.bus_types.find_one({"name": bt["name"]}):
            continue
        await db.bus_types.insert_one({
            **bt,
            "id": new_id(),
            "layout": _layout_for_bus_type(bt["name"]),
            "created_at": utcnow().isoformat(),
        })
        inserted += 1
    if inserted:
        logger.info("Seeded %d default bus types", inserted)


async def _ensure_indexes():
    # Also drop the earlier attempt at uniq_route_departure on schedules —
    # we now WANT multiple sibling schedules per (route, date, time) and rely
    # on bus_instance_id to link them, so this index is no longer valid.
    for stale in ("uniq_route_departure",):
        try:
            await db.schedules.drop_index(stale)
        except Exception:
            pass
    # Prevent double booking at DB level: unique (bus_instance_id, seat_number)
    # for non-released rows. All rows have bus_instance_id after startup
    # migration (falls back to schedule_id for legacy schedules).
    #
    # If legacy data has duplicate seat locks that now collide under
    # bus_instance_id (e.g. seat 5A locked on two sibling schedules that used
    # to be independent), the index build fails. In that case: log a warning,
    # keep the legacy `uniq_schedule_seat_active` index in place, and let the
    # operator resolve manually with `scripts/find_duplicate_seat_locks.py`.
    new_lock_index_ok = False
    try:
        await db.seat_locks.create_index(
            [("bus_instance_id", ASCENDING), ("seat_number", ASCENDING)],
            unique=True,
            partialFilterExpression={"status": {"$in": ["locked", "booked"]}},
            name="uniq_bus_instance_seat_active",
        )
        new_lock_index_ok = True
    except DuplicateKeyError as e:
        logger.warning(
            "\n" + ("=" * 78) + "\n"
            "  Legacy duplicate seat_locks detected — uniq_bus_instance_seat_active NOT built.\n"
            "  Backend will keep running with the legacy (schedule_id, seat_number) index.\n"
            "  Cross-sibling seat sharing is enforced in application code — DB-level guard\n"
            "  is off until conflicts are resolved.\n"
            "\n"
            "  To inspect + fix:\n"
            "    cd ~/app/backend && python scripts/find_duplicate_seat_locks.py\n"
            "    (add --release-expired to unlock stale non-booked locks; use --help for more)\n"
            "\n"
            f"  First conflict: {e.details.get('keyValue') if hasattr(e, 'details') else str(e)[:150]}\n"
            + ("=" * 78)
        )
    # Only drop the legacy index if the new one was built — otherwise we'd
    # leave the seat-locks collection with NO uniqueness at all.
    if new_lock_index_ok:
        for stale in ("uniq_schedule_seat_active",):
            try:
                await db.seat_locks.drop_index(stale)
            except Exception:
                pass
    await db.users.create_index([("email", ASCENDING)], unique=True)
    await db.bookings.create_index([("user_id", ASCENDING)])
    await db.bookings.create_index([("reference", ASCENDING)], unique=True)
    await db.bookings.create_index([("created_at", -1)])
    await db.bookings.create_index([("status", ASCENDING)])
    await db.terminals.create_index([("city", ASCENDING)])
    await db.schedules.create_index(
        [("from_terminal_id", ASCENDING), ("to_terminal_id", ASCENDING),
         ("departure_date", ASCENDING), ("departure_time", ASCENDING)]
    )
    # Shared seat pool per physical bus — see _migrate_bus_instance_ids docstring.
    await db.schedules.create_index([("bus_instance_id", ASCENDING)])
    await db.payment_transactions.create_index([("session_id", ASCENDING)], unique=True)
    await db.promo_codes.create_index([("code", ASCENDING)], unique=True)
    await db.audit_logs.create_index([("created_at", ASCENDING)])
    await db.audit_logs.create_index([("resource", ASCENDING), ("action", ASCENDING)])
    # Auto-expire login attempt records after the rate-limit window elapses
    await db.login_attempts.create_index("failed_at", expireAfterSeconds=LOGIN_WINDOW_SECONDS)
    await db.login_attempts.create_index([("identifier", ASCENDING)])
    await db.login_attempts.create_index([("ip", ASCENDING)])
    # Generic auth throttle (register / 2FA). TTL driven by per-doc `expires_at`.
    await db.auth_throttle.create_index("expires_at", expireAfterSeconds=0)
    await db.auth_throttle.create_index([("ip", ASCENDING), ("scope", ASCENDING)])
    # Password reset tokens: auto-expire at `expires_at` (per-doc TTL)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.password_reset_tokens.create_index([("user_id", ASCENDING)])
    await db.password_reset_tokens.create_index([("token_fp", ASCENDING)])
    # GoHub / CTS audit trail — indexes for post-mortem queries.
    await db.gohub_logs.create_index([("started_at", ASCENDING)])
    await db.gohub_logs.create_index([("operation", ASCENDING)])
    # Admin invites: auto-expire at `expires_at`
    await db.admin_invites.create_index("expires_at", expireAfterSeconds=0)
    await db.admin_invites.create_index([("email", ASCENDING)])
    await db.admin_invites.create_index([("token_fp", ASCENDING)])
    await db.feedback.create_index([("created_at", ASCENDING)])
    await db.feedback.create_index([("status", ASCENDING)])
    # Backfill child_fare on legacy schedules (defaults to half adult fare)
    try:
        await db.schedules.update_many(
            {"child_fare": {"$exists": False}},
            [{"$set": {"child_fare": {"$round": [{"$multiply": ["$adult_fare", 0.5]}, 2]}}}],
        )
    except Exception as e:
        logger.warning("child_fare backfill skipped: %s", e)


@api.get("/")
async def root():
    return {"service": "Transit E1 Bus Booking API", "status": "ok"}


@api.get("/health")
async def health():
    return {"status": "ok", "time": utcnow().isoformat()}


api.include_router(terminals_router.router)
api.include_router(promo_router.router)
api.include_router(feedback_router.router)
api.include_router(admin_users_router.router)
api.include_router(auth_router.router)
api.include_router(search_router.router)
api.include_router(seats_router.router)
api.include_router(bookings_router.router)
api.include_router(payments_router.router)
api.include_router(routes_router.router)
api.include_router(schedules_router.router)
api.include_router(bus_types_router.router)
api.include_router(gohub_router.router)
api.include_router(settings_router.router)
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    # We authenticate with Bearer JWTs (not cookies), so allow_credentials=False
    # keeps us safe with allow_origins='*' for dev. In production, lock
    # CORS_ORIGINS to your domain(s) and you can flip credentials on if needed.
    allow_credentials=False,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_start():
    # Run bus_instance_id backfill BEFORE _ensure_indexes so the new unique
    # index on (bus_instance_id, seat_number) has valid data to key on.
    await _seed_terminals()
    await _seed_admin()
    await _seed_schedules()
    await _migrate_sg_schedules()
    await _migrate_operator_names()
    await _migrate_bus_instance_ids()
    await _ensure_indexes()
    await _diversify_popular_schedules()
    await _seed_promos()
    await _seed_bus_types()
    # Safety net for Stripe webhook drop-offs / async payment settle delays.
    asyncio.create_task(_reconcile_loop())
    logger.info("Qistna Express startup complete")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
