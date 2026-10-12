"""Public search, popular route, and schedule detail endpoints."""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException

from core.config import db
from core.utils import _layout_for_bus_type

router = APIRouter()
_popular_cache: TTLCache = TTLCache(maxsize=8, ttl=30)

@router.get("/search")
async def search_schedules(
    date: str,  # YYYY-MM-DD
    from_terminal_id: Optional[str] = None,
    to_terminal_id: Optional[str] = None,
    from_city: Optional[str] = None,
    to_city: Optional[str] = None,
):
    """Search schedules for a date.

    Two modes:
      * Stop-level: caller passes `from_terminal_id` + `to_terminal_id`
        (existing behaviour — one specific pickup → one specific drop-off).
      * City-level: caller passes `from_city` and/or `to_city` — we expand
        the query to every terminal in that city. Used when the customer
        picks "Any stop in Kuala Lumpur" on the homepage.

    You can mix modes (e.g. city → specific terminal). At least one side of
    each pair must resolve to something or we return 400.
    """
    from_ids: List[str] = []
    to_ids: List[str] = []

    if from_terminal_id:
        from_ids = [from_terminal_id]
    elif from_city:
        from_ids = [t["id"] async for t in db.terminals.find(
            {"city": from_city, "is_pickup": {"$ne": False}}, {"_id": 0, "id": 1}
        )]
        # Backward-compat: terminals seeded before the is_pickup field default true.
        if not from_ids:
            from_ids = [t["id"] async for t in db.terminals.find({"city": from_city}, {"_id": 0, "id": 1})]
    if to_terminal_id:
        to_ids = [to_terminal_id]
    elif to_city:
        to_ids = [t["id"] async for t in db.terminals.find(
            {"city": to_city, "is_dropoff": {"$ne": False}}, {"_id": 0, "id": 1}
        )]
        if not to_ids:
            to_ids = [t["id"] async for t in db.terminals.find({"city": to_city}, {"_id": 0, "id": 1})]

    if not from_ids or not to_ids:
        raise HTTPException(400, "Provide either from_terminal_id or from_city (and same for `to`).")

    # Find schedules matching date + terminal set.
    if len(from_ids) == 1 and len(to_ids) == 1:
        query = {
            "from_terminal_id": from_ids[0],
            "to_terminal_id": to_ids[0],
            "departure_date": date,
        }
    else:
        query = {
            "from_terminal_id": {"$in": from_ids},
            "to_terminal_id": {"$in": to_ids},
            "departure_date": date,
        }

    # If searching for "today" in local Malaysia/Singapore time (UTC+8 — same offset for both),
    # hide buses whose departure time has already passed so users don't book a departed trip.
    local_tz = timezone(timedelta(hours=8))
    now_local = datetime.now(local_tz)
    today_local_iso = now_local.date().isoformat()
    if date == today_local_iso:
        query["departure_time"] = {"$gte": now_local.strftime("%H:%M")}

    # Exclude schedules whose bulk-creation `end_date` (last trip in the series)
    # has already passed. Rows without the field pass through unchanged.
    query["$or"] = [
        {"end_date": {"$exists": False}},
        {"end_date": None},
        {"end_date": ""},
        {"end_date": {"$gte": date}},
    ]

    schedules = await db.schedules.find(query, {"_id": 0}).sort("departure_time", 1).to_list(200)

    # -------------------------------------------------------------------------
    # Route-segment matching:
    #
    # A route-linked schedule stores from = route.origin, to = route.destination
    # (the whole physical bus). But customers might want any (pickup, dropoff)
    # pair the route's pairings advertise — e.g. "Melaka → JB" on a KL→SG bus.
    #
    # For each requested (pickup_id, dropoff_id) combination we also find route-
    # linked schedules whose route offers that pairing, then splice the pairing's
    # fare + specific segment into the returned row.
    # -------------------------------------------------------------------------
    matching_routes = await db.routes.find(
        {"is_active": {"$ne": False},
         "pairings.pickup_id": {"$in": from_ids},
         "pairings.dropoff_id": {"$in": to_ids}},
        {"_id": 0},
    ).to_list(200)
    # Build (pickup_id, dropoff_id) → pairing map per route so we know which segment
    # to attach for the specific customer query.
    segment_by_route: Dict[str, dict] = {}
    for r in matching_routes:
        for p in r.get("pairings", []):
            if p.get("pickup_id") in from_ids and p.get("dropoff_id") in to_ids:
                # Pick the first matching pairing per route (should be unique per pair).
                segment_by_route.setdefault(r["id"], {"route": r, "pairing": p})
    if segment_by_route:
        # Fetch schedules linked to any of these routes on this date.
        route_ids = list(segment_by_route.keys())
        seg_query: dict = {
            "route_id": {"$in": route_ids},
            "departure_date": date,
        }
        if "departure_time" in query:  # today-hides-past filter carries over
            seg_query["departure_time"] = query["departure_time"]
        seg_query["$or"] = query["$or"]  # end_date guard
        existing_ids = {s["id"] for s in schedules}
        seg_schedules = await db.schedules.find(seg_query, {"_id": 0}).to_list(200)
        for s in seg_schedules:
            if s["id"] in existing_ids:
                continue  # already returned via direct-match branch
            info = segment_by_route.get(s.get("route_id"))
            if not info:
                continue
            pairing = info["pairing"]
            # Overlay the sold segment on the row so the frontend renders correctly.
            s["from_terminal_id"] = pairing["pickup_id"]
            s["to_terminal_id"] = pairing["dropoff_id"]
            s["adult_fare"] = float(pairing.get("adult_fare", s.get("adult_fare", 0)))
            s["child_fare"] = float(pairing.get("child_fare", s.get("child_fare", 0)))
            s["currency"] = (pairing.get("currency") or s.get("currency") or "myr").lower()
            s["is_route_segment"] = True
            schedules.append(s)
        # Keep the result sorted by departure_time for a natural timeline view.
        schedules.sort(key=lambda x: x.get("departure_time", ""))

    # Enrich each schedule with the SPECIFIC pickup + drop-off terminal it uses.
    # Critical for city-level searches ("Any stop in KL → Any stop in SG") where
    # the user needs to know exactly which terminal to show up at.
    if schedules:
        terminal_ids = list({s["from_terminal_id"] for s in schedules} |
                            {s["to_terminal_id"] for s in schedules})
        term_docs = await db.terminals.find(
            {"id": {"$in": terminal_ids}},
            {"_id": 0, "id": 1, "code": 1, "name": 1, "city": 1, "landmark_address": 1},
        ).to_list(len(terminal_ids))
        term_by_id = {t["id"]: t for t in term_docs}
        for s in schedules:
            s["from_terminal"] = term_by_id.get(s["from_terminal_id"])
            s["to_terminal"] = term_by_id.get(s["to_terminal_id"])

    # Enrich with terminal names — for single-stop searches show the picked stop,
    # for city-level searches show the city as the label + count of options.
    if from_terminal_id:
        from_meta = await db.terminals.find_one({"id": from_terminal_id}, {"_id": 0})
    else:
        from_meta = {"city": from_city, "name": f"Any stop · {from_city}", "code": None,
                     "is_city": True, "stop_count": len(from_ids)}
    if to_terminal_id:
        to_meta = await db.terminals.find_one({"id": to_terminal_id}, {"_id": 0})
    else:
        to_meta = {"city": to_city, "name": f"Any stop · {to_city}", "code": None,
                   "is_city": True, "stop_count": len(to_ids)}

    # Single aggregation for booked-seat counts across ALL schedules in this result.
    # Now grouped by bus_instance_id — sibling schedules on the same physical bus
    # share the same seat pool, so seats_available reflects that shared count.
    if schedules:
        bus_keys = list({(s.get("bus_instance_id") or s["id"]) for s in schedules})
        pipeline = [
            {"$match": {"bus_instance_id": {"$in": bus_keys},
                        "status": {"$in": ["locked", "booked"]}}},
            {"$group": {"_id": "$bus_instance_id", "count": {"$sum": 1}}},
        ]
        counts = {row["_id"]: row["count"] async for row in db.seat_locks.aggregate(pipeline)}
        for s in schedules:
            key = s.get("bus_instance_id") or s["id"]
            s["seats_available"] = s["total_seats"] - counts.get(key, 0)

    return {
        "from": from_meta,
        "to": to_meta,
        "date": date,
        "schedules": schedules,
    }
POPULAR_PAIRS = [
    ("Kuala Lumpur", "Melaka"),
    ("Kuala Lumpur", "Johor Bahru"),
    ("Kuala Lumpur", "Penang"),
    ("Kuala Lumpur", "Singapore"),
    ("Penang", "Johor Bahru"),
    ("Singapore", "Kuala Lumpur"),
    ("Johor Bahru", "Kuala Lumpur"),
    ("Penang", "Kuala Lumpur"),
]


@router.get("/popular/now")
async def popular_now(limit: int = 6):
    """Return time-aware 'next bus' suggestions for popular city pairs.

    For each pair we pick the soonest upcoming schedule (today or next few days),
    enrich with terminals + seats-available + pricing. Uses local Malaysia/Singapore
    time (UTC+8) because schedule `departure_time` is stored as local HH:MM.

    Cached for 30 seconds — countdowns are computed client-side anyway, and seat
    counts on this cosmetic widget can lag a few seconds without harm.
    """
    limit = max(1, min(limit, 12))
    cache_key = f"limit:{limit}"
    if cache_key in _popular_cache:
        return _popular_cache[cache_key]

    local_tz = timezone(timedelta(hours=8))
    now_local = datetime.now(local_tz)
    now_utc = datetime.now(timezone.utc)
    today_iso = now_local.date().isoformat()
    current_hhmm = now_local.strftime("%H:%M")

    # Pre-load terminals indexed by city -> first terminal
    all_terms = await db.terminals.find({}, {"_id": 0}).to_list(500)
    city_to_term: dict = {}
    for t in all_terms:
        city_to_term.setdefault(t["city"], t)

    suggestions = []
    for from_city, to_city in POPULAR_PAIRS:
        f = city_to_term.get(from_city)
        t = city_to_term.get(to_city)
        if not f or not t:
            continue

        # Find nearest upcoming schedule (today after now, or any day in next 7 days)
        sched = await db.schedules.find_one(
            {
                "from_terminal_id": f["id"],
                "to_terminal_id": t["id"],
                "departure_date": today_iso,
                "departure_time": {"$gte": current_hhmm},
            },
            {"_id": 0},
            sort=[("departure_time", 1)],
        )
        if not sched:
            # Fall back to the next 7 days
            sched = await db.schedules.find_one(
                {
                    "from_terminal_id": f["id"],
                    "to_terminal_id": t["id"],
                    "departure_date": {"$gt": today_iso},
                },
                {"_id": 0},
                sort=[("departure_date", 1), ("departure_time", 1)],
            )
        if not sched:
            continue

        # Seats remaining — count against bus_instance to reflect shared pool.
        bus_key = sched.get("bus_instance_id") or sched["id"]
        booked_count = await db.seat_locks.count_documents(
            {"bus_instance_id": bus_key, "status": {"$in": ["locked", "booked"]}}
        )
        seats_available = sched["total_seats"] - booked_count

        # How many minutes until departure (positive only). Departure stored as local MY HH:MM.
        try:
            dep_dt = datetime.fromisoformat(
                f"{sched['departure_date']}T{sched['departure_time']}:00+08:00"
            )
            mins_until = max(0, int((dep_dt - now_local).total_seconds() // 60))
        except Exception:
            mins_until = None

        suggestions.append(
            {
                "from_city": from_city,
                "to_city": to_city,
                "from_terminal": f,
                "to_terminal": t,
                "schedule_id": sched["id"],
                "departure_date": sched["departure_date"],
                "departure_time": sched["departure_time"],
                "arrival_time": sched.get("arrival_time"),
                "is_today": sched["departure_date"] == today_iso,
                "minutes_until_departure": mins_until,
                "fare": sched.get("adult_fare"),
                "currency": sched.get("currency", "myr"),
                "seats_available": seats_available,
                "bus_type": sched.get("bus_type"),
                "operator": sched.get("bus_operator", "Qistna Express"),
            }
        )

    # Sort: today's first, then by minutes_until_departure asc
    suggestions.sort(
        key=lambda x: (
            0 if x["is_today"] else 1,
            x.get("minutes_until_departure") if x.get("minutes_until_departure") is not None else 10**9,
        )
    )
    payload = {"generated_at": now_utc.isoformat(), "items": suggestions[:limit]}
    _popular_cache[cache_key] = payload
    return payload


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str):
    sched = await db.schedules.find_one({"id": schedule_id}, {"_id": 0})
    if not sched:
        raise HTTPException(404, "Schedule not found")
    from_term = await db.terminals.find_one({"id": sched["from_terminal_id"]}, {"_id": 0})
    to_term = await db.terminals.find_one({"id": sched["to_terminal_id"]}, {"_id": 0})

    # Seat states — count locks against the SHARED bus_instance so seats
    # already sold on sibling schedules (e.g. KL→Melaka for a KL→JB search)
    # show as booked here too. Legacy schedules fall back to their own id.
    bus_key = sched.get("bus_instance_id") or sched["id"]
    locks = await db.seat_locks.find(
        {"bus_instance_id": bus_key, "status": {"$in": ["locked", "booked"]}},
        {"_id": 0},
    ).to_list(500)
    booked_seats = {lk["seat_number"]: lk["status"] for lk in locks}

    # Derive seat layout. Supports 2+2 (A,B | C,D = 4/row) and 2+1 (A,B | C = 3/row).
    # `layout_config` persisted on schedule overrides `bus_type` mapping.
    layout_config = sched.get("layout_config") or _layout_for_bus_type(sched.get("bus_type", "Standard"))
    cols_left, cols_right = (("A", "B"), ("C", "D")) if layout_config == "2+2" else (("A", "B"), ("C",))
    per_row = len(cols_left) + len(cols_right)

    total_seats = int(sched.get("total_seats") or (sched.get("rows", 10) * 4))
    # Last row may be partial when total_seats is not a clean multiple of per_row.
    full_rows = total_seats // per_row
    remainder = total_seats - full_rows * per_row

    layout = []
    seats_emitted = 0

    def seat_cell(sn: str):
        return {"seat_number": sn, "status": booked_seats.get(sn, "available")}

    for r in range(1, full_rows + 1):
        row_seats = []
        for col in cols_left:
            row_seats.append(seat_cell(f"{r}{col}"))
            seats_emitted += 1
        row_seats.append({"seat_number": None, "aisle": True})
        for col in cols_right:
            row_seats.append(seat_cell(f"{r}{col}"))
            seats_emitted += 1
        layout.append(row_seats)

    # Partial last row (e.g. 27-seater 2+1 → 9 full rows; or 2+2 with 39 seats → last row 3 seats)
    if remainder > 0:
        r = full_rows + 1
        row_seats = []
        remaining = remainder
        for col in cols_left:
            if remaining <= 0:
                row_seats.append({"seat_number": None, "empty": True})
                continue
            row_seats.append(seat_cell(f"{r}{col}"))
            remaining -= 1
            seats_emitted += 1
        row_seats.append({"seat_number": None, "aisle": True})
        for col in cols_right:
            if remaining <= 0:
                row_seats.append({"seat_number": None, "empty": True})
                continue
            row_seats.append(seat_cell(f"{r}{col}"))
            remaining -= 1
            seats_emitted += 1
        layout.append(row_seats)

    return {
        "schedule": sched,
        "from": from_term,
        "to": to_term,
        "layout": layout,
        "layout_config": layout_config,
        "seats_per_row": per_row,
    }
