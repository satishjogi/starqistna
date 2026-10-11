"""Pure pricing calculation shared by bookings and promo validation."""
from typing import List, Optional


def _calc_pricing(sched: dict, passengers: List[dict], promo: Optional[dict] = None) -> dict:
    adult_fare = float(sched["adult_fare"])
    # Prefer per-schedule child fare; fallback to half of adult for legacy schedules.
    child_fare = float(sched["child_fare"]) if sched.get("child_fare") is not None else round(adult_fare * 0.5, 2)
    adults = sum(1 for p in passengers if p["category"] == "adult")
    children = sum(1 for p in passengers if p["category"] == "child")
    subtotal = round(adults * adult_fare + children * child_fare, 2)

    discount = 0.0
    promo_info = None
    if promo:
        if promo["type"] == "percent":
            discount = round(subtotal * (promo["value"] / 100.0), 2)
        else:
            discount = min(round(float(promo["value"]), 2), subtotal)
        discount = max(0.0, min(discount, subtotal))
        promo_info = {
            "code": promo["code"],
            "type": promo["type"],
            "value": promo["value"],
            "discount_amount": discount,
        }

    total = round(subtotal - discount, 2)
    return {
        "adult_fare": adult_fare,
        "child_fare": child_fare,
        "adults": adults,
        "children": children,
        "subtotal": subtotal,
        "discount": discount,
        "promo": promo_info,
        "total": total,
        "currency": sched.get("currency", "myr"),
    }
