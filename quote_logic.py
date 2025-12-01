# quote_logic.py

from typing import Dict, Any
from dataclasses import dataclass


@dataclass
class TaxConfig:
    default_rate: float = 0.06  # 6% as a simple default


TAX_CONFIG = TaxConfig()


def determine_tax_rate(zip_code: str) -> float:
    """
    Very simple tax logic (you can refine later).
    For now, treat DC / MD / VA in your area as ~6%.
    """
    zip_str = str(zip_code or "").strip()

    if not zip_str:
        return TAX_CONFIG.default_rate

    # You can customize this by prefixes if you want
    return TAX_CONFIG.default_rate


def price_tv_mounting(tv_size: int) -> float:
    """
    Base TV mounting pricing (no wall-type or add-ons yet):

    - Under 60 inches: $60
    - 60 inches or more: $70
    """
    if tv_size <= 0:
        return 0.0

    if tv_size < 60:
        return 60.0
    else:
        return 70.0


def adjust_for_wall_type(base_price: float, wall_type: str) -> float:
    """
    Simple wall-type adjustments (you can tweak numbers):

    - drywall: +$0
    - brick:   +$20
    - concrete / stone / tile: +$30
    """
    wt = (wall_type or "").lower()

    if wt in ("brick",):
        return base_price + 20.0
    elif wt in ("concrete", "stone", "tile", "tile/stone"):
        return base_price + 30.0
    else:
        # drywall or unknown
        return base_price


def adjust_for_concealment(base_price: float, conceal_type: str) -> float:
    """
    Concealment adjustments (you can tweak):

    - none:      +$0
    - on_wall:   +$40
    - in_wall:   +$80
    """
    ct = (conceal_type or "").lower()

    if ct in ("on_wall", "on-wall", "raceway"):
        return base_price + 40.0
    elif ct in ("in_wall", "in-wall"):
        return base_price + 80.0
    else:
        return base_price


def price_picture_hanging(picture_count: int) -> float:
    """
    Picture & Art Hanging pricing:

    - 1–3 pictures: $40
    - 4–5 pictures: $60
    - >5 pictures:  $60 + $10 per additional picture beyond 5
    """
    count = max(0, int(picture_count))

    if count == 0:
        return 0.0
    elif count <= 3:
        return 40.0
    elif count <= 5:
        return 60.0
    else:
        extra = count - 5
        return 60.0 + 10.0 * extra


def price_floating_shelves(has_shelves: bool) -> float:
    """
    Simple placeholder pricing for floating shelves as part of the visit.
    For now this is a flat amount if shelves are included; the shelf count is
    carried through to the result for display but does not change the price.
    """
    return 50.0 if has_shelves else 0.0


def price_closet_shelving(closet_shelving: bool, closet_needs_materials: bool) -> float:
    """
    Closet Shelving (aka closet organizers):

    - Base labor (closet_shelving = True): $80

    For now, closet_needs_materials and closet_shelf_count are informational and
    do NOT change the price automatically. You can tweak this later.
    """
    if not closet_shelving:
        return 0.0

    base = 80.0
    return base


def price_decor(decor_count: int) -> float:
    """
    Decor / Art & Mirror Arrangement:

    Simple per-item pricing for now:
    - $15 per piece
    """
    count = max(0, int(decor_count))
    if count == 0:
        return 0.0
    return 15.0 * count


def price_tv_addons(base_price: float, soundbar: bool, led: bool) -> float:
    """
    Add-on pricing for TV mounting:

    - Soundbar: +$20
    - LED lights: +$10
    """
    total = base_price
    if soundbar:
        total += 20.0
    if led:
        total += 10.0
    return total


def calculate_quote(
    *,
    service: str,
    tv_size: int,
    wall_type: str,
    conceal_type: str,
    soundbar: bool,
    shelves: bool,
    picture_count: int,
    led: bool,
    same_day: bool,
    after_hours: bool,
    zip_code: str,
    closet_shelving: bool = False,
    closet_needs_materials: bool = False,
    decor_count: int = 0,
    shelves_count: int = 0,
    closet_shelf_count: int = 0,
    closet_shelf_not_sure: bool = False,
) -> Dict[str, Any]:
    """
    Main quote calculator.

    IMPORTANT:
    - `service` is the primary service (for labeling), but pricing is built
      from the individual components (TV, pictures, shelves, closet, decor).
    - This means you can mix & match services in one visit (multi-service).

    Shelf counts are carried through for clarity on the quote, but they do
    not currently change pricing automatically.
    """

    # 1) TV Mounting (can be primary OR add-on)
    tv_base = price_tv_mounting(tv_size)
    tv_with_wall = adjust_for_wall_type(tv_base, wall_type)
    tv_with_concealment = adjust_for_concealment(tv_with_wall, conceal_type)
    tv_total = price_tv_addons(tv_with_concealment, soundbar, led)

    if tv_size <= 0:
        tv_total = 0.0

    # 2) Picture Hanging
    picture_total = price_picture_hanging(picture_count)

    # 3) Floating Shelves
    shelves_total = price_floating_shelves(shelves)

    # 4) Closet Shelving (Closet Organizers)
    closet_total = price_closet_shelving(closet_shelving, closet_needs_materials)

    # 5) Decor / Art & Mirror Arrangement
    decor_total = price_decor(decor_count)

    # 6) Multi-service discount (optional, small)
    chargeable_components = [
        tv_total > 0,
        picture_total > 0,
        shelves_total > 0,
        closet_total > 0,
        decor_total > 0,
    ]
    num_services = sum(1 for c in chargeable_components if c)
    multi_service_discount = 0.0
    if num_services >= 2:
        # e.g., small $10 discount for booking 2+ services at once
        multi_service_discount = -10.0

    # 7) Same-day / after-hours surcharges in the quote
    # To avoid double-charging (you already add surcharges in booking),
    # keep these at 0.0 for now.
    same_day_surcharge = 0.0
    after_hours_surcharge = 0.0

    # 8) Sum everything
    line_items = {
        "tv_total": round(tv_total, 2),
        "picture_total": round(picture_total, 2),
        "shelves_total": round(shelves_total, 2),
        "closet_total": round(closet_total, 2),
        "decor_total": round(decor_total, 2),
        "multi_service_discount": round(multi_service_discount, 2),
        "same_day_surcharge": round(same_day_surcharge, 2),
        "after_hours_surcharge": round(after_hours_surcharge, 2),
    }

    subtotal_before_tax = round(
        tv_total
        + picture_total
        + shelves_total
        + closet_total
        + decor_total
        + multi_service_discount
        + same_day_surcharge
        + after_hours_surcharge,
        2,
    )

    tax_rate = determine_tax_rate(zip_code)
    tax_amount = round(subtotal_before_tax * tax_rate, 2)
    estimated_total_with_tax = round(subtotal_before_tax + tax_amount, 2)

    return {
        "service": service,
        "line_items": line_items,
        "subtotal_before_tax": subtotal_before_tax,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "estimated_total_with_tax": estimated_total_with_tax,
        "num_services": num_services,

        # extra context surfaced on the result page
        "closet_needs_materials": closet_needs_materials,
        "decor_count": decor_count,
        "picture_count": picture_count,
        "tv_size": tv_size,
        "shelves_count": shelves_count,
        "closet_shelf_count": closet_shelf_count,
        "closet_shelf_not_sure": closet_shelf_not_sure,
    }
