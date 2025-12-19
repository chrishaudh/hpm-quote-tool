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


# =========================
# TV MOUNTING PRICING
# =========================
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


def price_tv_removal(tv_remove_count: int) -> float:
    """
    TV removal pricing:
    - $5 per TV removed (or relocated/replaced).
    """
    count = max(0, int(tv_remove_count))
    return 5.0 * count


# =========================
# PICTURE & ART HANGING
# =========================
def price_picture_hanging_base(picture_count: int) -> float:
    """
    Picture & Art Hanging pricing (base):

    - 1–2 items: $30
    - 3–5 items: $60
    - 6–8 items: $90
    - Every 3 items after 2 increases by $30.

    Logic:
      remaining_after_two = max(0, count - 2)
      groups_after_two = ceil(remaining_after_two / 3)
      price = 30 * (1 + groups_after_two)
    """
    count = max(0, int(picture_count))

    if count == 0:
        return 0.0

    if count <= 2:
        return 30.0

    remaining = count - 2
    groups_after_two = (remaining + 2) // 3  # ceiling(remaining / 3)
    return 30.0 * (1 + groups_after_two)


def price_large_picture_addon(picture_large_count: int) -> float:
    """
    Additional pricing for large pieces (>= 5 ft wide):

    - 1–2 large pieces: +$10
    - 3–4 large pieces: +$20
    - etc. → $10 per pair
    """
    count = max(0, int(picture_large_count))
    if count == 0:
        return 0.0

    pairs = (count + 1) // 2  # ceiling(count / 2)
    return 10.0 * pairs


# =========================
# FLOATING SHELVES
# =========================
def price_floating_shelves_by_count(shelves_count: int) -> float:
    """
    Floating shelves pricing (install):

    - 1–2 shelves: $60
    - 3–4 shelves: $120
    - 5–6 shelves: $180
    - etc.

    Rule: every 2 shelves is an additional $60.
    """
    count = max(0, int(shelves_count))
    if count == 0:
        return 0.0

    blocks = (count + 1) // 2  # ceiling(count / 2)
    return 60.0 * blocks


def price_shelf_removal(shelves_remove_count: int) -> float:
    """
    Floating shelf removal pricing:
    - $5 per shelf removed (or relocated/replaced).
    """
    count = max(0, int(shelves_remove_count))
    return 5.0 * count


# =========================
# CLOSET SHELVING / ORGANIZERS
# =========================
def price_closet_shelving_by_count(closet_shelf_count: int) -> float:
    """
    Closet shelving pricing (install):

    - 1 shelf: $60
    - 2 shelves: $90
    - 3 shelves: $120
    - Each additional shelf after 2 adds $30.
    """
    count = max(0, int(closet_shelf_count))
    if count == 0:
        return 0.0
    if count == 1:
        return 60.0
    return 90.0 + max(0, count - 2) * 30.0


def price_closet_removal(closet_remove_count: int) -> float:
    """
    Closet shelf removal pricing:
    - $10 per shelf removed (or relocated/replaced).
    """
    count = max(0, int(closet_remove_count))
    return 10.0 * count


# =========================
# CURTAINS / BLINDS / DECOR
# =========================
def price_decor_install(decor_count: int) -> float:
    """
    Decor / Curtains / Blinds installation:

    - $15 per piece (curtain/blind/decor).
    """
    count = max(0, int(decor_count))
    if count == 0:
        return 0.0
    return 15.0 * count


def price_decor_removal(decor_remove_count: int) -> float:
    """
    Curtains / blinds removal pricing:
    - $10 per item removed (or relocated/replaced).
    """
    count = max(0, int(decor_remove_count))
    return 10.0 * count


# =========================
# TIME ESTIMATE (for quote)
# =========================
def estimate_hours(
    tv_count: int,
    tv_remove_count: int,
    shelves_count: int,
    shelves_remove_count: int,
    picture_count: int,
    picture_large_count: int,
    closet_shelf_count: int,
    closet_remove_count: int,
    decor_count: int,
    decor_remove_count: int,
) -> float:
    """
    Rough estimate of on-site time in hours, based on your rules & typical pace.

    This does NOT change the booking calendar yet; it's for the quote display
    (and can later be wired into booking duration logic).
    """
    h = 0.0

    # TVs: ~1 hr per TV, 15 min per removal
    h += max(0, int(tv_count)) * 1.0
    h += max(0, int(tv_remove_count)) * 0.25

    # Floating shelves: ~2 hours for 3 shelves ≈ 0.66 hr each
    h += max(0, int(shelves_count)) * (2.0 / 3.0)
    h += max(0, int(shelves_remove_count)) * 0.25

    # Pictures: ~9–10 minutes each, bump for large pieces
    h += max(0, int(picture_count)) * 0.15
    h += max(0, int(picture_large_count)) * 0.1

    # Closet shelving: ~30 minutes each, 15 min per removal
    h += max(0, int(closet_shelf_count)) * 0.5
    h += max(0, int(closet_remove_count)) * 0.25

    # Curtains / decor: ~9–10 minutes each, same for removals
    h += max(0, int(decor_count)) * 0.15
    h += max(0, int(decor_remove_count)) * 0.15

    # Setup / walkthrough buffer
    h += 0.25

    # Clamp between 1.5 and 8 hours
    if h < 1.5:
        h = 1.5
    if h > 8:
        h = 8.0

    return round(h, 1)


# =========================
# MAIN QUOTE CALCULATION
# =========================
import math
def estimate_tv_hours(tv_count: int, tv_remove_count: int) -> float:
    """
    TVs
    - Mounting: 1 hour per TV
    - Removal:  15 minutes (0.25 hour) per TV
    """
    tv_count = max(0, int(tv_count))
    tv_remove_count = max(0, int(tv_remove_count))

    mount_hours = tv_count * 1.0
    remove_hours = tv_remove_count * 0.25

    return mount_hours + remove_hours


def estimate_picture_hours(picture_count: int) -> float:
    """
    Art / Pictures:
    - Every 3 items takes 30 minutes (0.5h)
    - BUT minimum booking slot is 1 hour:
      * 1–3  items -> 0.5h work, but book 1.0h
      * 4–6  items -> 1.0h
      * 7–9  items -> 1.5h
      * and so on
    """
    count = max(0, int(picture_count))
    if count == 0:
        return 0.0

    groups_of_three = math.ceil(count / 3.0)
    raw_hours = 0.5 * groups_of_three
    return max(1.0, raw_hours)


def estimate_shelf_hours(shelves_count: int, shelves_remove_count: int) -> float:
    """
    Floating Shelves:
    - Every 2 items takes 30 minutes (0.5h)
    - Minimum booking slot is 1 hour
      * 1–2  shelves -> 0.5h work, but book 1.0h
      * 3–4  shelves -> 1.0h
      * 5–6  shelves -> 1.5h
    - Removal: 15 minutes (0.25h) per shelf
    """
    shelves_count = max(0, int(shelves_count))
    shelves_remove_count = max(0, int(shelves_remove_count))

    if shelves_count > 0:
        groups_of_two = math.ceil(shelves_count / 2.0)
        raw_install_hours = 0.5 * groups_of_two
        install_hours = max(1.0, raw_install_hours)
    else:
        install_hours = 0.0

    remove_hours = shelves_remove_count * 0.25

    return install_hours + remove_hours


def estimate_closet_hours(closet_shelf_count: int, closet_remove_count: int) -> float:
    """
    Closet Shelving:
    - 30 minutes (0.5h) per shelf
    - Minimum booking slot is 1 hour
      * 1 shelf  -> 0.5h work, book 1.0h
      * 2 shelves -> 1.0h
      * 3 shelves -> 1.5h
    - Removal: 20 minutes (1/3h) per shelf
    """
    closet_shelf_count = max(0, int(closet_shelf_count))
    closet_remove_count = max(0, int(closet_remove_count))

    if closet_shelf_count > 0:
        install_hours = max(1.0, 0.5 * closet_shelf_count)
    else:
        install_hours = 0.0

    remove_hours = closet_remove_count * (1.0 / 3.0)  # ~0.33h each

    return install_hours + remove_hours


def estimate_curtains_hours(decor_count: int, decor_remove_count: int) -> float:
    """
    Curtains / Blinds / Decor:
    - 30 minutes (0.5h) per item
    - Minimum booking slot is 1 hour
      * 1 item  -> 0.5h work, book 1.0h
      * 2 items -> 1.0h
    - Removal: 20 minutes (1/3h) per item
    """
    decor_count = max(0, int(decor_count))
    decor_remove_count = max(0, int(decor_remove_count))

    if decor_count > 0:
        install_hours = max(1.0, 0.5 * decor_count)
    else:
        install_hours = 0.0

    remove_hours = decor_remove_count * (1.0 / 3.0)

    return install_hours + remove_hours

def calculate_quote(
    *,
    service: str,
    tv_size: int,
    tv_count: int,
    tv_sizes: list[int] | None = None,
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
    tv_remove_count: int = 0,
    shelves_remove_count: int = 0,
    closet_remove_count: int = 0,
    decor_remove_count: int = 0,
    picture_large_count: int = 0,
    ladder_required: bool = False,
    parking_notes: str = "",
    preferred_contact: str = "",
    gallery_wall: bool = False,
) -> Dict[str, Any]:
    """
    Main quote calculator.

    - Pricing uses your flat-fee per-item rules.
    - estimated_hours uses your time assumptions per service and removal.
    """

    # ----------------------------
    # 1) TV Mounting pricing
    # ----------------------------
    tv_sizes = tv_sizes or []
    tv_sizes_clean = [max(0, int(x)) for x in tv_sizes if int(x) > 0]

    # If tv_sizes were supplied, derive tv_count from them
    if tv_sizes_clean:
        tv_count = len(tv_sizes_clean)

    # Base TV labor = sum(per-TV price by its size)
    base_tv_price = 0.0
    if tv_sizes_clean:
        for size in tv_sizes_clean:
            base_tv_price += (60.0 if size < 60 else 70.0)
    else:
        # fallback to legacy single size * tv_count
        tv_count = max(0, int(tv_count))
        tv_size_val = max(0, int(tv_size))
        if tv_size_val > 0 and tv_count > 0:
            per_tv = 60.0 if tv_size_val < 60 else 70.0
            base_tv_price = per_tv * tv_count

    # wall type & concealment adjustments (applied to total TV labor)
    tv_with_wall = adjust_for_wall_type(base_tv_price, wall_type)
    tv_with_concealment = adjust_for_concealment(tv_with_wall, conceal_type)

    # addons (apply once per visit as you do today)
    tv_with_addons = price_tv_addons(tv_with_concealment, soundbar, led)

    tv_remove_count = max(0, int(tv_remove_count))
    tv_remove_total = tv_remove_count * 5.0

    tv_total = tv_with_addons + tv_remove_total

    # ----------------------------
    # 2) Picture & Art Hanging pricing
    # ----------------------------
    # New rule:
    # - $30 minimum
    # - 1–2 items = $30
    # - 3–5 items = $60
    # - 6–8 items = $90
    # - every 3 items after 2 adds +$30
    pic_count = max(0, int(picture_count))
    if pic_count == 0:
        picture_total = 0.0
    else:
        blocks_after_two = max(0, pic_count - 2)
        groups_of_three = math.ceil(blocks_after_two / 3.0)
        picture_total = 30.0 + 30.0 * groups_of_three

    # Large pieces (>6ft) add-ons: +$10 per pair
    picture_large_count = max(0, int(picture_large_count))
    if picture_large_count > 0:
        large_pairs = math.ceil(picture_large_count / 2.0)
        picture_large_total = 10.0 * large_pairs
    else:
        picture_large_total = 0.0

    picture_total += picture_large_total

    # ----------------------------
    # 3) Floating Shelves pricing
    # ----------------------------
    # - Every 2 shelves is +$60
    #   * 1–2 shelves = $60
    #   * 3–4 shelves = $120
    #   * 5–6 shelves = $180 ...
    shelf_count = max(0, int(shelves_count))
    shelves_price = 0.0
    if shelves and shelf_count > 0:
        shelf_blocks = math.ceil(shelf_count / 2.0)
        shelves_price = 60.0 * shelf_blocks

    # Floating shelf removal: $5 per shelf
    shelves_remove_count = max(0, int(shelves_remove_count))
    shelves_remove_total = shelves_remove_count * 5.0

    shelves_total = shelves_price + shelves_remove_total

    # ----------------------------
    # 4) Closet shelving pricing
    # ----------------------------
    # Closet labor:
    # - 1 shelf = $60
    # - 2 shelves = $90
    # - 3 shelves = $120
    # - After 2 shelves, each additional shelf +$30
    closet_shelf_count = max(0, int(closet_shelf_count))
    closet_labor_total = 0.0
    if closet_shelving and closet_shelf_count > 0:
        if closet_shelf_count == 1:
            closet_labor_total = 60.0
        else:
            closet_labor_total = 90.0 + max(0, closet_shelf_count - 2) * 30.0

    # Closet removal: $10 per shelf
    closet_remove_count = max(0, int(closet_remove_count))
    closet_remove_total = closet_remove_count * 10.0

    closet_total = closet_labor_total + closet_remove_total

    # ----------------------------
    # 5) Curtains / Blinds / Decor pricing
    # ----------------------------
    # - $60 minimum for any work (except 0)
    # - Simple per-item component; we’ll use $20/item with $60 min
    decor_count = max(0, int(decor_count))
    if decor_count == 0:
        decor_labor_total = 0.0
    else:
        decor_labor_total = max(60.0, 20.0 * decor_count)

    # Curtains / blinds removal: $10 each
    decor_remove_count = max(0, int(decor_remove_count))
    decor_remove_total = decor_remove_count * 10.0

    decor_total = decor_labor_total + decor_remove_total

    # ----------------------------
    # 6) Multi-service discount (15%)
    # ----------------------------
    # Count how many service "buckets" have >0 charges
    service_subtotals = [
        tv_total,
        picture_total,
        shelves_total,
        closet_total,
        decor_total,
    ]
    num_services = sum(1 for v in service_subtotals if v > 0)

    multi_service_discount = 0.0
    gross_before_discount = sum(service_subtotals)

    if num_services >= 2 and gross_before_discount > 0:
        multi_service_discount = round(-0.15 * gross_before_discount, 2)

    # ----------------------------
    # 7) Same-day / after-hours surcharges (quote side)
    # ----------------------------
    # Keep these at 0.0 in the quote to avoid double-charging;
    # they're applied on the booking side when a time is chosen.
    same_day_surcharge = 0.0
    after_hours_surcharge = 0.0

    # ----------------------------
    # 8) Subtotal + tax
    # ----------------------------
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

    # ----------------------------
    # 9) Estimated hours based on your timing rules
    # ----------------------------
    tv_hours = estimate_tv_hours(tv_count, tv_remove_count)
    picture_hours = estimate_picture_hours(picture_count)
    shelf_hours = estimate_shelf_hours(shelves_count, shelves_remove_count)
    closet_hours = estimate_closet_hours(closet_shelf_count, closet_remove_count)
    curtains_hours = estimate_curtains_hours(decor_count, decor_remove_count)

    estimated_hours = tv_hours + picture_hours + shelf_hours + closet_hours + curtains_hours

    # Clamp to a reasonable range
    if estimated_hours <= 0:
        estimated_hours = 1.0
    estimated_hours = min(estimated_hours, 8.0)

    # ----------------------------
    # 10) Build result
    # ----------------------------
    line_items = {
        "tv_total": round(tv_total, 2),
        "tv_remove_total": round(tv_remove_total, 2),
        "picture_total": round(picture_total, 2),
        "picture_large_total": round(picture_large_total, 2),
        "shelves_total": round(shelves_total, 2),
        "shelves_remove_total": round(shelves_remove_total, 2),
        "closet_total": round(closet_total, 2),
        "closet_remove_total": round(closet_remove_total, 2),
        "decor_total": round(decor_total, 2),
        "decor_remove_total": round(decor_remove_total, 2),
        "multi_service_discount": round(multi_service_discount, 2),
        "same_day_surcharge": round(same_day_surcharge, 2),
        "after_hours_surcharge": round(after_hours_surcharge, 2),
    }

    return {
        "service": service,
        "line_items": line_items,
        "subtotal_before_tax": subtotal_before_tax,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "estimated_total_with_tax": estimated_total_with_tax,
        "num_services": num_services,
        "estimated_hours": round(estimated_hours, 2),

        # extra context surfaced on the result / booking side
        "closet_needs_materials": closet_needs_materials,
        "decor_count": decor_count,
        "picture_count": pic_count,
        "picture_large_count": picture_large_count,
        "tv_size": tv_size_val,
        "tv_sizes": tv_sizes_clean,
        "tv_count": tv_count,
        "tv_remove_count": tv_remove_count,
        "shelves_count": shelf_count,
        "shelves_remove_count": shelves_remove_count,
        "closet_shelf_count": closet_shelf_count,
        "closet_shelf_not_sure": closet_shelf_not_sure,
        "closet_remove_count": closet_remove_count,
        "decor_remove_count": decor_remove_count,
        "ladder_required": ladder_required,
        "parking_notes": parking_notes,
        "preferred_contact": preferred_contact,
        "gallery_wall": gallery_wall,
    }

