def lookup_tax_rate(zip_code: str) -> float:
    """
    Return the sales tax rate as a decimal based on ZIP code.
    Simple internal mapping for DMV area around 20735.
    """
    z = str(zip_code).strip()

    if len(z) < 5 or not z.isdigit():
        return 0.06  # default fallback

    prefix3 = z[:3]

    TAX_BY_PREFIX = {
        # Maryland (statewide 6%)
        "206": 0.06,
        "207": 0.06,
        "208": 0.06,

        # Washington, DC (6.5%)
        "200": 0.065,
        "203": 0.065,
        "204": 0.065,
        "205": 0.065,

        # Northern Virginia (close-in NoVA, ~6%)
        "220": 0.06,
        "221": 0.06,
        "222": 0.06,
        "223": 0.06,
    }

    return TAX_BY_PREFIX.get(prefix3, 0.06)


def calculate_quote(
    service: str,
    tv_size: int = 0,
    wall_type: str = "drywall",
    conceal_type: str = "none",
    soundbar: bool = False,
    shelves: bool = False,
    picture_count: int = 0,
    led: bool = False,
    same_day: bool = False,
    after_hours: bool = False,
    zip_code: str = "20735",
) -> dict:
    """
    Multi-service quote calculation.

    service:
      - "tv_mounting"
      - "picture_hanging"
      - "floating_shelves"
      - "closet_organizers"
      - "decor"
    """

    wall_fee_map = {
        "drywall": 0,
        "plaster": 20,
        "brick": 30,
        "tile": 40,
    }
    wall_fee = wall_fee_map.get(wall_type, 0)

    base = 0
    conceal_fee = 0
    addons = 0
    discount = 0

    # Always treat picture_count as "number of items" for non-TV work
    items = max(picture_count, 0)

    # ---- TV MOUNTING SERVICE ----
    if service == "tv_mounting":
        # Base by TV size
        if tv_size and tv_size <= 60:
            base = 75
        elif tv_size and tv_size <= 85:
            base = 95
        else:
            # Very large TV or missing size: use a safe higher base
            base = 120

        # Concealment only matters for TV jobs
        conceal_fee_map = {
            "none": 0,
            "raceway": 25,
            "inwall": 60,
        }
        conceal_fee = conceal_fee_map.get(conceal_type, 0)

        services_selected = 1  # TV is 1 service

        if soundbar:
            addons += 20
            services_selected += 1

        # shelves on the same visit
        if shelves:
            addons += 25
            services_selected += 1

        # pictures / décor as add-on to TV job
        if items > 0:
            addons += 20 + max(items - 1, 0) * 10
            services_selected += 1

        if led:
            addons += 15
            services_selected += 1

        # Multi-service discount only for TV combos
        if services_selected >= 3:
            discount = 20
        elif services_selected == 2:
            discount = 10

    # ---- PICTURE HANGING & DECOR ONLY ----
    elif service in ("picture_hanging", "decor"):
        # Base covers up to 2 items
        if items <= 0:
            items = 1
        base = 40
        if items > 2:
            base += (items - 2) * 10
        # No concealment here
        conceal_fee = 0
        discount = 0

    # ---- FLOATING SHELVES ONLY ----
    elif service == "floating_shelves":
        # Interpret items as number of shelves
        if items <= 0:
            items = 1
        base = 50 + max(items - 1, 0) * 25
        conceal_fee = 0
        discount = 0

    # ---- CLOSET ORGANIZERS ----
    elif service == "closet_organizers":
        # Flat base for typical closet, items can represent sections
        base = 120
        if items > 1:
            base += (items - 1) * 20
        conceal_fee = 0
        discount = 0

    else:
        # Fallback: treat as basic service
        base = 75
        conceal_fee = 0
        discount = 0

    # Same-day / after-hours apply to all services
    if same_day:
        addons += 15
    if after_hours:
        addons += 20

    subtotal = base + wall_fee + conceal_fee + addons - discount

    tax_rate = lookup_tax_rate(zip_code)
    total_with_tax = subtotal * (1 + tax_rate)
    final_total = round(total_with_tax)

    return {
        "line_items": {
            "base_mounting": base,
            "wall_type_adjustment": wall_fee,
            "wire_concealment": conceal_fee,
            "addons": addons,
            "multi_service_discount": -discount,
        },
        "tax_rate": tax_rate,
        "subtotal_before_tax": subtotal,
        "estimated_total_with_tax": final_total,
    }
