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


# -----------------------------
# TV mounting helpers
# -----------------------------
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
    Simple wall-type adjustments:

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
    Concealment adjustments:

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


# -----------------------------
# Picture & Art Hanging
# -----------------------------
def price_picture_hanging(picture_count: int, picture_large_count: int = 0) -> float:
    """
    Picture & Art Hanging pricing:

    Base tiers (with a $30 minimum):
    - 1–2 items:  $30
    - 3–5 items:  $60
    - 6–8 items:  $90
    - >8 items:   90 + $30 for every additional group of 3

    Large pieces (>5 ft wide):
    - +$10 per 2 large pieces (rounded up)
    """
    count = max(0, int(picture_count))

    if count == 0:
        return 0.0

    # Base tiers
    if count <= 2:
        base = 30.0
    elif count <= 5:
        base = 60.0
    elif count <= 8:
        base = 90.0
    else:
        extra_pieces = count - 8
        extra_groups = (extra_pieces + 2) // 3  # groups of 3
        base = 90.0 + 30.0 * extra_groups

    # Large-piece surcharge
    large = max(0, int(picture_large_count))
    if large > 0:
        large_groups = (large + 1) // 2  # every 2 large pieces
        base += 10.0 * large_groups

    return base


# -----------------------------
# Floating Shelves
# -----------------------------
def price_floating_shelves(
    shelves_install_count: int, shelves_remove_count: int
) -> float:
    """
    Floating Shelves pricing:

    Install:
      Every 2 shelves = $60
      e.g. 1–2 = 60, 3–4 = 120, 5–6 = 180, etc.

    Removal:
      $5 per removed shelf
    """
    install = max(0, int(shelves_install_count))
    remove = max(0, int(shelves_remove_count))

    if install > 0:
        groups = (install + 1) // 2  # ceil(install / 2)
        install_total = 60.0 * groups
    else:
        install_total = 0.0

    removal_total = 5.0 * remove

    return install_total + removal_total


# -----------------------------
# Closet Shelving / Organizers
# -----------------------------
def price_closet_shelving(
    closet_install_count: int,
    closet_remove_count: int,
) -> float:
    """
    Closet Shelving pricing:

    Install:
      1 shelf = $60
      2 shelves = $90
      3 shelves = $120
      Each additional shelf after 2 adds $30

    Removal:
      $10 per removed shelf
    """
    install = max(0, int(closet_install_count))
    remove = max(0, int(closet_remove_count))

    if install > 0:
        # 1 shelf: 60; 2: 90; 3: 120; etc.
        install_total = 60.0 + max(0, install - 1) * 30.0
    else:
        install_total = 0.0

    removal_total = 10.0 * remove

    return install_total + removal_total


# -----------------------------
# Curtains / Blinds / Decor
# -----------------------------
def price_decor(decor_count: int) -> float:
    """
    Curtains / Blinds / Decor pricing:

    - $15 per piece
    - $60 minimum once any items are included
    """
    count = max(0, int(decor_count))
    if count == 0:
        return 0.0

    base = 15.0 * count
    return max(60.0, base)


# -----------------------------
# Main quote calculator
# -----------------------------
def calculate_quote(
    *,
    service: str,
    tv_size: int,
    wall_type: str,
    conceal_type: str,
    soundbar: bool,
    shelves: bool,
    picture_count: int,
    picture_large_count: int = 0,
    gallery_wall: bool = False,
    led: bool = False,
    same_day: bool = False,
    after_hours: bool = False,
    zip_code: str,
    closet_shelving: bool = False,
    closet_needs_materials: bool = False,
    decor_count: int = 0,
    shelves_install_count: int = 0,
    shelves_remove_count: int = 0,
    closet_install_count: int = 0,
    closet_remove_count: int = 0,
    closet_shelf_not_sure: bool = False,
) -> Dict[str, Any]:
    """
    Main quote calculator.

    `service` is the primary label, but pricing is built from
    the individual components so you can mix services in one visit.
    """

    # 1) TV Mounting
    tv_base = price_tv_mounting(tv_size)
    tv_with_wall = adjust_for_wall_type(tv_base, wall_type)
    tv_with_concealment = adjust_for_concealment(tv_with_wall, conceal_type)
    tv_total = price_tv_addons(tv_with_concealment, soundbar, led)

    if tv_size <= 0:
        tv_total = 0.0

    # 2) Picture & Art Hanging
    picture_total = price_picture_hanging(picture_count, picture_large_count)

    # 3) Floating Shelves
    if shelves:
        shelves_total = price_floating_shelves(
            shelves_install_count=shelves_install_count,
            shelves_remove_count=shelves_remove_count,
        )
    else:
        shelves_total = 0.0

    # 4) Closet Shelving / Organizers
    if closet_shelving:
        closet_total = price_closet_shelving(
            closet_install_count=closet_install_count,
            closet_remove_count=closet_remove_count,
        )
    else:
        closet_total = 0.0

    # 5) Curtains / Blinds / Decor
    decor_total = price_decor(decor_count)

    # 6) Multi-service discount
    # Count how many services actually have a charge
    service_totals = [tv_total, picture_total, shelves_total, closet_total, decor_total]
    num_services = sum(1 for t in service_totals if t > 0)

    multi_service_discount = 0.0
    if num_services >= 2:
        base_sum = sum(t for t in service_totals if t > 0)
        multi_service_discount = round(-0.15 * base_sum, 2)  # 15% off

    # 7) Same-day / after-hours surcharges in the quote
    # These are handled on the booking side so we keep them at 0.0 here.
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
        "picture_large_count": picture_large_count,
        "gallery_wall": gallery_wall,
        "tv_size": tv_size,
        "shelves_install_count": shelves_install_count,
        "shelves_remove_count": shelves_remove_count,
        "closet_install_count": closet_install_count,
        "closet_remove_count": closet_remove_count,
        "closet_shelf_not_sure": closet_shelf_not_sure,
    }
