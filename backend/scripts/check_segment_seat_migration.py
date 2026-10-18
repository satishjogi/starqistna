#!/usr/bin/env python3
"""Read-only preflight for the multi-stop per-leg seat-inventory migration.

Run from backend/ on a backup/clone first, then on the live database immediately
before a controlled rollout:

    python scripts/check_segment_seat_migration.py

This script never updates or deletes database records. It calculates the exact
per-leg keys the startup migration will need to reserve and exits non-zero when
it finds unresolved overlaps or records it cannot map safely.
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

try:
    import server  # noqa: E402
except Exception as exc:  # noqa: BLE001
    print(f"Cannot load backend configuration: {exc}", file=sys.stderr)
    print("Run from backend/ after verifying backend/.env is configured.", file=sys.stderr)
    raise SystemExit(2)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _run() -> int:
    db = server.db
    now = datetime.now(timezone.utc)

    schedules = {
        item["id"]: item
        async for item in db.schedules.find({}, {"_id": 0})
        if item.get("id")
    }
    routes = {
        item["id"]: item
        async for item in db.routes.find({}, {"_id": 0})
        if item.get("id")
    }
    bookings = {
        item["id"]: item
        async for item in db.bookings.find({}, {"_id": 0})
        if item.get("id")
    }

    collisions: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    errors: list[str] = []
    represented_booking_seats: set[tuple[str, str]] = set()
    active_locks = 0
    active_holds = 0
    stale_holds = 0
    mapped_confirmed_seats = 0
    reconstructed_confirmed_seats = 0

    def record(bus_id: str, leg: int, seat: str, occupant: dict) -> None:
        collisions[(bus_id, int(leg), seat)].append(occupant)

    def resolve_legs(schedule: dict, pickup: str | None, dropoff: str | None, source: str) -> list[int] | None:
        route = routes.get(schedule.get("route_id"))
        if schedule.get("route_id") and not route:
            errors.append(f"{source}: linked route {schedule.get('route_id')} is missing")
            return None
        try:
            return server._segment_leg_indices_for_schedule(schedule, pickup, dropoff, route)
        except ValueError as exc:
            errors.append(f"{source}: cannot map route segment ({pickup} -> {dropoff}): {exc}")
            return None

    # Primary rows represent both temporary holds and existing booked seat locks.
    async for lock in db.seat_locks.find(
        {"status": {"$in": ["locked", "booked"]}}
    ):
        status = lock.get("status")
        if status == "locked":
            expiry = _parse_time(lock.get("expires_at"))
            if expiry is None or expiry <= now:
                stale_holds += 1
                continue
            active_holds += 1

        schedule = schedules.get(lock.get("schedule_id"))
        if not schedule:
            errors.append(
                f"active lock {lock.get('id', '<no-id>')}: schedule {lock.get('schedule_id')} is missing"
            )
            continue

        booking = bookings.get(lock.get("booking_id")) if lock.get("booking_id") else None
        pickup = (
            lock.get("segment_from_terminal_id")
            or (booking or {}).get("from_terminal_id")
            or schedule.get("from_terminal_id")
        )
        dropoff = (
            lock.get("segment_to_terminal_id")
            or (booking or {}).get("to_terminal_id")
            or schedule.get("to_terminal_id")
        )
        legs = lock.get("leg_indices")
        if not isinstance(legs, list) or not legs:
            legs = resolve_legs(schedule, pickup, dropoff, f"lock {lock.get('id', '<no-id>')}")
        if not legs:
            continue

        bus_id = (
            lock.get("bus_instance_id")
            or schedule.get("bus_instance_id")
            or schedule.get("id")
        )
        seat = str(lock.get("seat_number") or "").strip().upper()
        if not bus_id or not seat:
            errors.append(f"active lock {lock.get('id', '<no-id>')}: missing bus or seat number")
            continue

        active_locks += 1
        booking_id = lock.get("booking_id")
        if booking_id:
            represented_booking_seats.add((booking_id, seat))
        ref = (booking or {}).get("reference") or booking_id
        occupant = {
            "label": f"booking {ref}" if booking_id else f"hold {str(lock.get('lock_token') or lock.get('id') or 'unknown')[-8:]}",
            # Distinguish duplicate legacy lock rows even when they point at the
            # same booking; startup migration cannot create two active rows for
            # one bus/leg/seat key.
            "occupant_key": f"lock:{lock.get('id') or lock.get('_id') or lock.get('lock_token')}",
            "booking_id": booking_id,
            "lock_token": lock.get("lock_token"),
            "source": "seat_locks",
            "status": status,
            "from": pickup,
            "to": dropoff,
        }
        for leg in legs:
            record(str(bus_id), int(leg), seat, occupant)

    # Confirmed bookings are authoritative: rebuild their reservations even when
    # the old application lost/deleted the primary seat lock.
    async for booking in db.bookings.find(
        {"status": "confirmed", "seats": {"$exists": True}}, {"_id": 0}
    ):
        schedule = schedules.get(booking.get("schedule_id"))
        if not schedule:
            errors.append(
                f"confirmed booking {booking.get('reference') or booking.get('id')}: "
                f"schedule {booking.get('schedule_id')} is missing"
            )
            continue
        pickup = booking.get("from_terminal_id") or schedule.get("from_terminal_id")
        dropoff = booking.get("to_terminal_id") or schedule.get("to_terminal_id")
        legs = booking.get("leg_indices")
        if not isinstance(legs, list) or not legs:
            legs = resolve_legs(schedule, pickup, dropoff, f"booking {booking.get('reference') or booking.get('id')}")
        if not legs:
            continue

        bus_id = (
            booking.get("bus_instance_id")
            or schedule.get("bus_instance_id")
            or schedule.get("id")
        )
        booking_id = booking.get("id")
        for raw_seat in booking.get("seats") or []:
            seat = str(raw_seat or "").strip().upper()
            if not seat:
                continue
            if booking_id and (booking_id, seat) in represented_booking_seats:
                mapped_confirmed_seats += 1
                continue

            reconstructed_confirmed_seats += 1
            occupant = {
                "label": f"confirmed booking {booking.get('reference') or booking_id}",
                "occupant_key": f"booking:{booking_id}:{seat}",
                "booking_id": booking_id,
                "lock_token": None,
                "source": "confirmed_bookings_without_active_lock",
                "status": "confirmed",
                "from": pickup,
                "to": dropoff,
            }
            for leg in legs:
                record(str(bus_id), int(leg), seat, occupant)

    # If this preflight is run after a previous migration attempt, check the
    # secondary leg inventory too. Orphan rows can otherwise stop index creation
    # or conceal a mismatch between the primary and per-leg lock collections.
    if "seat_leg_locks" in await db.list_collection_names():
        async for leg_lock in db.seat_leg_locks.find(
            {"status": {"$in": ["locked", "booked"]}}, {"_id": 0}
        ):
            if leg_lock.get("status") == "locked":
                expiry = _parse_time(leg_lock.get("expires_at"))
                if expiry is None or expiry <= now:
                    continue
            key = (
                str(leg_lock.get("bus_instance_id") or ""),
                int(leg_lock.get("leg_index", -1)),
                str(leg_lock.get("seat_number") or "").strip().upper(),
            )
            matching = collisions.get(key, [])
            if not matching:
                errors.append(
                    f"orphan active seat_leg_locks row: bus={key[0]} leg={key[1]} "
                    f"seat={key[2]} booking={leg_lock.get('booking_id') or 'hold'}"
                )
                continue
            leg_booking_id = leg_lock.get("booking_id")
            leg_token = leg_lock.get("lock_token")
            owner_match = any(
                (leg_booking_id and item.get("booking_id") == leg_booking_id)
                or (not leg_booking_id and leg_token and item.get("lock_token") == leg_token)
                for item in matching
            )
            if not owner_match:
                errors.append(
                    f"per-leg reservation owner mismatch: bus={key[0]} leg={key[1]} "
                    f"seat={key[2]} booking={leg_booking_id or 'hold'}"
                )

    conflicts = {
        key: occupants for key, occupants in collisions.items()
        if len({item.get("occupant_key") or item.get("label") for item in occupants}) > 1
    }

    print("Starqistna multi-stop seat migration preflight (READ-ONLY)")
    print(f"Schedules inspected:                         {len(schedules)}")
    print(f"Routes inspected:                            {len(routes)}")
    print(f"Confirmed bookings inspected:                {sum(1 for b in bookings.values() if b.get('status') == 'confirmed')}")
    print(f"Active primary lock rows mapped:             {active_locks}")
    print(f"Unexpired temporary holds:                   {active_holds}")
    print(f"Expired/malformed locked rows to be released:{stale_holds:>5}")
    print(f"Confirmed booking seats already represented: {mapped_confirmed_seats}")
    print(f"Confirmed booking seats needing reconstruction: {reconstructed_confirmed_seats}")
    print(f"Conflicting bus/leg/seat keys:                {len(conflicts)}")
    print(f"Unmappable or inconsistent records:           {len(errors)}")

    for (bus_id, leg_index, seat), occupants in list(conflicts.items())[:50]:
        print(f"\nCONFLICT bus={bus_id} leg={leg_index} seat={seat}")
        for item in occupants:
            print(
                f"  - {item['label']} ({item['status']}; "
                f"{item['from']} -> {item['to']}; source={item['source']})"
            )
    if len(conflicts) > 50:
        print(f"\n... {len(conflicts) - 50} additional conflict key(s) omitted")

    for error in errors[:50]:
        print(f"\nERROR: {error}")
    if len(errors) > 50:
        print(f"\n... {len(errors) - 50} additional error(s) omitted")

    if conflicts or errors:
        print("\nRESULT: NOT READY. Do not deploy until every conflict/error is resolved.")
        return 1
    print("\nRESULT: STRUCTURALLY CLEAR. This is not a substitute for a tested backup/restore.")
    print("No records were written, updated, or deleted.")
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(_run())
    finally:
        server.client.close()
    raise SystemExit(exit_code)
