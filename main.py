from typing import Optional
from datetime import datetime, date, timedelta
import re

import requests  # make sure this is in requirements.txt
import pytz

from fastapi import FastAPI, Request, Form, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from google_calendar import create_booking_event, get_available_slots_for_date
from quote_logic import calculate_quote
from config import TIMEZONE

app = FastAPI(title="Hawkins Pro Mounting Quote API")

templates = Jinja2Templates(directory="templates")

# ---------------------------------
# Service + Zapier configuration
# ---------------------------------

SERVICE_TYPES = [
    "TV Mounting",
    "Picture & Art Hanging",
    "Floating Shelves",
    "Curtains & Blinds",
    "Closet Shelving",
]

SERVICE_AREA_STATES = {"DC", "MD", "VA"}

ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")

SAME_DAY_SURCHARGE = 25.0
AFTER_HOURS_SURCHARGE = 20.0  # $20 flat after-hours
AFTER_HOURS_START_HOUR = 17   # 5 PM

ZAPIER_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/25408903/uz2ihgh/"
BOOKING_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/25408903/ukipdo4/"

CALENDAR_ID = "primary"


# =====================================================
# Helper: compute booking duration based on services
#            + detailed counts
# =====================================================
def compute_booking_duration_hours(
    include_tv: bool,
    include_pictures: bool,
    include_shelves: bool,
    include_closet: bool,
    include_decor: bool,
    picture_total_count: int,
    picture_large_count: int,
    shelves_install_count: int,
    shelves_remove_count: int,
    closet_install_count: int,
    closet_remove_count: int,
    decor_item_count: int,
) -> float:
    """
    Estimate a time window using the selected services and counts.

    This is intentionally conservative so you have enough time on-site.
    """

    duration = 0.0

    # TV mounting – assume 1 main TV in most jobs.
    if include_tv:
        duration += 1.5  # baseline for one TV, wall type, and possible wire work

    # Picture & Art Hanging
    if include_pictures:
        total = max(0, int(picture_total_count))
        large = max(0, int(picture_large_count))

        if total > 0:
            if total <= 2:
                duration += 1.0
            elif total <= 5:
                duration += 1.5
            elif total <= 8:
                duration += 2.0
            else:
                # every additional 3 pieces beyond 8 ≈ +0.5 hr
                extra_sets = (total - 8 + 2) // 3
                duration += 2.0 + 0.5 * extra_sets

        # Larger than 5 ft wide – adds some handling / measuring time
        if large > 0:
            # Roughly +0.25 hr per 2 large pieces
            large_sets = (large + 1) // 2
            duration += 0.25 * large_sets

    # Floating shelves
    if include_shelves:
        install = max(0, int(shelves_install_count))
        remove = max(0, int(shelves_remove_count))

        if install > 0:
            # ~0.6 hr per shelf, based on 3 shelves ≈ 2 hours
            duration += 0.6 * install

        if remove > 0:
            # Removal is quicker than install but still some patching/cleanup
            duration += 0.2 * remove

    # Closet shelving / organizers
    if include_closet:
        install_c = max(0, int(closet_install_count))
        remove_c = max(0, int(closet_remove_count))

        if install_c > 0:
            # ~0.5 hr per closet shelf (planning, leveling, hardware)
            duration += 0.5 * install_c
        if remove_c > 0:
            # 15 minutes per shelf removal
            duration += 0.25 * remove_c

    # Curtains / Blinds / Decor
    if include_decor:
        decor = max(0, int(decor_item_count))
        if decor > 0:
            # ~0.4 hr per item for measuring, drilling, leveling
            duration += 0.4 * decor

    # Fallback: if somehow nothing added, assume a 2-hour minimum window
    if duration <= 0.0:
        duration = 2.0

    # Clamp window between 2 and 6 hours for sanity
    if duration < 2.0:
        duration = 2.0
    if duration > 6.0:
        duration = 6.0

    # Round to nearest half-hour
    duration = round(duration * 2) / 2.0
    return duration


# =====================================================
# Address validation helper (separate fields)
# =====================================================
def validate_address(
    street: str,
    city: str,
    state: str,
    zip_code: str,
):
    """
    Basic address validation for separate inputs.

    Returns:
        (is_valid: bool, parsed: dict, error_message: str)
    """
    street = (street or "").strip()
    city = (city or "").strip()
    state = (state or "").strip().upper()
    zip_code = (zip_code or "").strip()

    if not street:
        return False, {}, "Please enter the street address."
    if not city:
        return False, {}, "Please enter the city."
    if not state:
        return False, {}, "Please enter the state (e.g. DC, MD, VA)."
    if not zip_code:
        return False, {}, "Please enter the ZIP code."

    # State: must be 2 letters
    if len(state) != 2 or not state.isalpha():
        return (
            False,
            {},
            "State should be a 2-letter abbreviation (e.g. DC, MD, VA).",
        )

    # ZIP: must match pattern
    if not ZIP_RE.match(zip_code):
        return (
            False,
            {},
            "ZIP code should be 5 digits (e.g. 20001).",
        )

    # Optional: enforce service area
    if SERVICE_AREA_STATES and state not in SERVICE_AREA_STATES:
        return (
            False,
            {},
            "This address appears to be outside our service area (DC, Maryland, Northern Virginia). Please double-check or contact us.",
        )

    parsed = {
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
    }

    return True, parsed, ""


# =====================================================
# TEST ROUTE
# =====================================================
@app.get("/test-book")
async def test_book():
    tz = pytz.timezone(TIMEZONE)
    start = tz.localize(datetime.now() + timedelta(hours=1))
    end = start + timedelta(hours=2)

    create_booking_event(
        summary="Test Booking",
        description="This is a test booking from FastAPI.",
        start_dt=start,
        end_dt=end,
        customer_email=None,
    )

    return {"status": "ok"}


# =====================================================
# AVAILABILITY API
# =====================================================
@app.get("/api/availability")
async def api_get_availability(
    service_date: str = Query(..., description="Date in YYYY-MM-DD"),
):
    try:
        dt: date = datetime.strptime(service_date, "%Y-%m-%d").date()
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid date format"})

    slots = get_available_slots_for_date(CALENDAR_ID, dt)
    return {"date": service_date, "slots": slots}


# =====================================================
# BOOKING FORM (GET)
# =====================================================
@app.get("/book", response_class=HTMLResponse)
async def show_booking_form(
    request: Request,
    service_type: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    email: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
):
    prefilled = {
        "service_type": service_type,
        "name": name,
        "email": email,
        "phone": phone,
        "address_street": "",
        "address_city": "",
        "address_state": "",
        "address_zip": "",
    }

    return templates.TemplateResponse(
        "booking_form.html",
        {
            "request": request,
            "service_types": SERVICE_TYPES,
            "prefilled": prefilled,
            "errors": {},
        },
    )


# =====================================================
# BOOKING FORM (POST)
# =====================================================
@app.post("/book", response_class=HTMLResponse)
async def submit_booking(
    request: Request,
    background_tasks: BackgroundTasks,
    service_type: str = Form(...),

    # New style: ISO datetime string from availability dropdown
    time_slot: Optional[str] = Form(None),

    # Legacy style (kept just in case)
    appointment_date: Optional[str] = Form(None),
    appointment_time: Optional[str] = Form(None),

    # Multi-service selections for this visit
    include_tv: str = Form("false"),
    include_pictures: str = Form("false"),
    include_shelves: str = Form("false"),
    include_closet: str = Form("false"),
    include_decor: str = Form("false"),

    # Detailed counts / flags from booking_form.html
    picture_total_count: int = Form(0),
    picture_has_large: str = Form("false"),
    picture_large_count: int = Form(0),
    gallery_wall: str = Form("false"),

    shelves_install_count: int = Form(0),
    shelves_remove_count: int = Form(0),

    closet_install_count: int = Form(0),
    closet_remove_count: int = Form(0),

    decor_item_count: int = Form(0),

    # Customer info
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),

    # NEW: Separate address fields
    address_street: str = Form(...),
    address_city: str = Form(...),
    address_state: str = Form(...),
    address_zip: str = Form(...),

    # Extra preferences
    contact_method: str = Form(""),
    ladder_required: str = Form("false"),
    building_notes: str = Form(""),

    notes: str = Form(""),
):
    tz = pytz.timezone(TIMEZONE)

    def to_bool(value: str) -> bool:
        return str(value).lower() == "true"

    # 0) Validate address using separate fields
    is_valid_address, parsed_address, addr_error = validate_address(
        address_street,
        address_city,
        address_state,
        address_zip,
    )
    if not is_valid_address:
        prefilled = {
            "service_type": service_type,
            "name": name,
            "email": email,
            "phone": phone,
            "address_street": address_street,
            "address_city": address_city,
            "address_state": address_state,
            "address_zip": address_zip,
        }
        errors = {"address": addr_error}

        return templates.TemplateResponse(
            "booking_form.html",
            {
                "request": request,
                "service_types": SERVICE_TYPES,
                "prefilled": prefilled,
                "errors": errors,
            },
            status_code=400,
        )

    # Build full address string for calendar + Zapier + confirmation page
    full_address = (
        f"{parsed_address['street']}, "
        f"{parsed_address['city']}, "
        f"{parsed_address['state']} {parsed_address['zip']}"
    )

    # 1) Determine start datetime
    if time_slot:
        try:
            start_dt = datetime.fromisoformat(time_slot)
        except ValueError:
            return templates.TemplateResponse(
                "booking_error.html",
                {
                    "request": request,
                    "message": "Invalid time slot selected. Please go back and try again.",
                },
                status_code=400,
            )
    else:
        if not appointment_date or not appointment_time:
            return templates.TemplateResponse(
                "booking_error.html",
                {
                    "request": request,
                    "message": "Missing appointment date/time. Please go back and select a time.",
                },
                status_code=400,
            )
        try:
            start_dt = datetime.fromisoformat(f"{appointment_date}T{appointment_time}")
        except ValueError:
            return templates.TemplateResponse(
                "booking_error.html",
                {
                    "request": request,
                    "message": "Invalid date or time format. Please go back and try again.",
                },
                status_code=400,
            )

    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    else:
        start_dt = start_dt.astimezone(tz)

    # 1b) Interpret multi-service checkboxes
    include_tv_bool = to_bool(include_tv)
    include_pictures_bool = to_bool(include_pictures)
    include_shelves_bool = to_bool(include_shelves)
    include_closet_bool = to_bool(include_closet)
    include_decor_bool = to_bool(include_decor)

    # 1c) Interpret flags
    picture_has_large_bool = to_bool(picture_has_large)
    gallery_wall_bool = to_bool(gallery_wall)
    ladder_required_bool = to_bool(ladder_required)

    # 2) Determine end datetime (dynamic with counts)
    duration_hours = compute_booking_duration_hours(
        include_tv_bool,
        include_pictures_bool,
        include_shelves_bool,
        include_closet_bool,
        include_decor_bool,
        picture_total_count,
        picture_large_count,
        shelves_install_count,
        shelves_remove_count,
        closet_install_count,
        closet_remove_count,
        decor_item_count,
    )
    end_dt = start_dt + timedelta(hours=duration_hours)

    # 2b) Same-day / after-hours flags
    now_local = datetime.now(tz)
    is_same_day_booking = (start_dt.date() == now_local.date())
    is_after_hours_booking = start_dt.hour >= AFTER_HOURS_START_HOUR

    # 3) Build calendar event details
    summary = f"{service_type} - {name}"
    description_lines = [
        f"Service (primary): {service_type}",
        f"Customer: {name}",
        f"Email: {email}",
        f"Phone: {phone}",
        f"Address: {full_address}",
    ]

    # Detailed service breakdown
    services_this_visit = []
    if include_tv_bool:
        services_this_visit.append("TV Mounting")
    if include_pictures_bool:
        services_this_visit.append("Picture & Art Hanging")
    if include_shelves_bool:
        services_this_visit.append("Floating Shelves")
    if include_closet_bool:
        services_this_visit.append("Closet Shelving / Organizers")
    if include_decor_bool:
        services_this_visit.append("Curtains / Blinds / Decor")

    num_services = len(services_this_visit)

    if services_this_visit:
        description_lines.append("Services this visit: " + ", ".join(services_this_visit))
        description_lines.append(f"Number of services: {num_services}")

    # Counts + flags for planning
    if include_pictures_bool:
        description_lines.append(
            f"Picture/Art: total={picture_total_count}, "
            f"larger_than_5ft={picture_large_count}, "
            f"gallery_wall={'YES' if gallery_wall_bool else 'NO'}"
        )

    if include_shelves_bool:
        description_lines.append(
            f"Floating shelves: install={shelves_install_count}, "
            f"remove={shelves_remove_count}"
        )

    if include_closet_bool:
        description_lines.append(
            f"Closet shelving: install={closet_install_count}, "
            f"remove={closet_remove_count}"
        )

    if include_decor_bool:
        description_lines.append(f"Curtains/Blinds/Decor items: {decor_item_count}")

    # Preferences
    if contact_method:
        description_lines.append(f"Preferred contact: {contact_method}")
    description_lines.append(
        f"Ladder required: {'YES' if ladder_required_bool else 'NO'}"
    )
    if building_notes:
        description_lines.append(f"Parking / building notes: {building_notes}")

    if notes:
        description_lines.append(f"Project notes: {notes}")

    description_lines.append(f"Expected duration: {duration_hours:.1f} hours")
    description_lines.append(
        f"Same-day booking: {'YES' if is_same_day_booking else 'NO'}"
    )
    description_lines.append(
        f"After-hours booking (start >= {AFTER_HOURS_START_HOUR}:00): "
        f"{'YES' if is_after_hours_booking else 'NO'}"
    )

    description = "\n".join(description_lines)

    # 4) Create the calendar event
    create_booking_event(
        summary=summary,
        description=description,
        start_dt=start_dt,
        end_dt=end_dt,
        customer_email=email,
        calendar_id=CALENDAR_ID,
    )

    # 5) Trigger email confirmation via Zapier
    background_tasks.add_task(
        send_booking_to_zapier,
        name,
        email,
        phone,
        full_address,
        service_type,
        start_dt,
        end_dt,
        notes,
        parsed_address,
        services_this_visit,
        duration_hours,
        num_services,
        picture_total_count,
        picture_large_count,
        picture_has_large_bool,
        gallery_wall_bool,
        shelves_install_count,
        shelves_remove_count,
        closet_install_count,
        closet_remove_count,
        decor_item_count,
        contact_method,
        ladder_required_bool,
        building_notes,
    )

        # 6) Show confirmation page
    return templates.TemplateResponse(
        "booking_confirm.html",
        {
            "request": request,
            "name": name,
            "service_type": service_type,
            "start": start_dt,
            "end": end_dt,
            "address": full_address,
            "services_this_visit": services_this_visit,
            "num_services": num_services,
            "duration_hours": duration_hours,
            # New context for the confirmation template
            "picture_total_count": picture_total_count,
            "picture_large_count": picture_large_count if picture_has_large_bool else 0,
            "gallery_wall": gallery_wall_bool,
            "shelves_install_count": shelves_install_count,
            "shelves_remove_count": shelves_remove_count,
            "closet_install_count": closet_install_count,
            "closet_remove_count": closet_remove_count,
            "decor_item_count": decor_item_count,
            "ladder_required": ladder_required_bool,
            "contact_method": contact_method,
            "building_notes": building_notes,
        },
    )


# =====================================================
# Pydantic model for JSON quote requests
# =====================================================
class QuoteRequest(BaseModel):
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None

    service: str = "tv_mounting"

    tv_size: int = 0
    wall_type: str = "drywall"
    conceal_type: str = "none"
    soundbar: bool = False
    led: bool = False

    # Floating shelves
    shelves: bool = False
    shelves_install_count: int = 0
    shelves_remove_count: int = 0

    # Picture & art
    picture_count: int = 0
    picture_large_count: int = 0
    gallery_wall: bool = False

    # Closet shelving
    closet_shelving: bool = False
    closet_needs_materials: bool = False
    closet_install_count: int = 0
    closet_remove_count: int = 0
    closet_shelf_not_sure: bool = False

    # Curtains / blinds / decor
    decor_count: int = 0

    same_day: bool = False
    after_hours: bool = False

    zip_code: str = "20735"


# =====================================================
# Zapier sending helper for QUOTES
# =====================================================
def send_lead_to_zapier(
    contact_name: Optional[str],
    contact_phone: Optional[str],
    contact_email: Optional[str],
    service: str,
    tv_size: int,
    wall_type: str,
    conceal_type: str,
    picture_count: int,
    same_day: bool,
    after_hours: bool,
    zip_code: str,
    booking_url: str,
    quote_result: dict,
) -> None:
    if not ZAPIER_WEBHOOK_URL:
        print("ZAPIER_WEBHOOK_URL is empty; skipping Zapier send")
        return

    try:
        line_items = quote_result.get("line_items", {}) or {}

        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "contact_name": contact_name or "",
            "contact_phone": contact_phone or "",
            "contact_email": contact_email or "",
            "zip_code": zip_code,
            "service": service,
            "tv_size": tv_size,
            "wall_type": wall_type,
            "conceal_type": conceal_type,
            "items_count": picture_count,
            "same_day": same_day,
            "after_hours": after_hours,
            "booking_url": booking_url,
            "base_mounting": line_items.get("base_mounting", 0),
            "wall_type_adjustment": line_items.get("wall_type_adjustment", 0),
            "wire_concealment": line_items.get("wire_concealment", 0),
            "addons": line_items.get("addons", 0),
            "multi_service_discount": line_items.get("multi_service_discount", 0),
            "tax_rate": quote_result.get("tax_rate", 0),
            "subtotal_before_tax": quote_result.get("subtotal_before_tax", 0),
            "estimated_total_with_tax": quote_result.get(
                "estimated_total_with_tax", 0
            ),
        }

        print("📤 Payload sending to Zapier (quote):")
        print(payload)

        resp = requests.post(ZAPIER_WEBHOOK_URL, json=payload, timeout=5)
        resp.raise_for_status()
        print("✅ Lead sent to Zapier successfully")

    except Exception as e:
        print(f"❌ Error sending lead to Zapier: {e}")


# =====================================================
# Zapier sending helper for BOOKINGS
# =====================================================
def send_booking_to_zapier(
    name: str,
    email: str,
    phone: str,
    address: str,
    service_type: str,
    start_dt: datetime,
    end_dt: datetime,
    notes: str,
    parsed_address: dict,
    services_this_visit: list,
    duration_hours: float,
    num_services: int,
    picture_total_count: int,
    picture_large_count: int,
    picture_has_large: bool,
    gallery_wall: bool,
    shelves_install_count: int,
    shelves_remove_count: int,
    closet_install_count: int,
    closet_remove_count: int,
    decor_item_count: int,
    contact_method: str,
    ladder_required: bool,
    building_notes: str,
) -> None:
    if not BOOKING_WEBHOOK_URL:
        print("BOOKING_WEBHOOK_URL is empty; skipping booking Zapier send")
        return

    try:
        tz = pytz.timezone(TIMEZONE)
        start_local = start_dt.astimezone(tz)
        end_local = end_dt.astimezone(tz)
        now_local = datetime.now(tz)

        is_same_day = (start_local.date() == now_local.date())
        is_after_hours = start_local.hour >= AFTER_HOURS_START_HOUR

        same_day_surcharge = SAME_DAY_SURCHARGE if is_same_day else 0.0
        after_hours_surcharge = AFTER_HOURS_SURCHARGE if is_after_hours else 0.0
        total_surcharge = same_day_surcharge + after_hours_surcharge

        services_str = ", ".join(services_this_visit) if services_this_visit else ""

        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "name": name,
            "email": email,
            "phone": phone,
            "address": address,
            "address_street": parsed_address.get("street", ""),
            "address_city": parsed_address.get("city", ""),
            "address_state": parsed_address.get("state", ""),
            "address_zip": parsed_address.get("zip", ""),
            "service_type": service_type,
            "start_iso": start_local.isoformat(),
            "end_iso": end_local.isoformat(),
            "start_pretty_date": start_local.strftime("%A, %B %-d, %Y"),
            "start_pretty_time": start_local.strftime("%-I:%M %p"),
            "end_pretty_time": end_local.strftime("%-I:%M %p"),
            "is_same_day": is_same_day,
            "is_after_hours": is_after_hours,
            "same_day_surcharge": same_day_surcharge,
            "after_hours_surcharge": after_hours_surcharge,
            "total_surcharge": total_surcharge,
            "services_this_visit": services_str,
            "num_services": num_services,
            "duration_hours": duration_hours,
            "notes": notes or "",
            # New detailed fields
            "picture_total_count": picture_total_count,
            "picture_large_count": picture_large_count,
            "picture_has_large": picture_has_large,
            "gallery_wall": gallery_wall,
            "shelves_install_count": shelves_install_count,
            "shelves_remove_count": shelves_remove_count,
            "closet_install_count": closet_install_count,
            "closet_remove_count": closet_remove_count,
            "decor_item_count": decor_item_count,
            "contact_method": contact_method,
            "ladder_required": ladder_required,
            "building_notes": building_notes,
        }

        print("📤 Payload sending to Zapier (booking):")
        print(payload)

        resp = requests.post(BOOKING_WEBHOOK_URL, json=payload, timeout=5)
        resp.raise_for_status()
        print("✅ Booking sent to Zapier successfully")

    except Exception as e:
        print(f"❌ Error sending booking to Zapier: {e}")


# =====================================================
# Booking URL helper (front-end link into /book)
# =====================================================
def build_booking_url(
    contact_name: str,
    contact_email: str,
    contact_phone: str,
    service: str,
) -> str:
    base_url = "/book"

    params = []
    if contact_name:
        params.append(f"name={contact_name}")
    if contact_email:
        params.append(f"email={contact_email}")
    if contact_phone:
        params.append(f"phone={contact_phone}")
    if service:
        service_map = {
            "tv_mounting": "TV Mounting",
            "picture_hanging": "Picture & Art Hanging",
            "floating_shelves": "Floating Shelves",
            "closet_shelving": "Closet Shelving",
            "decor": "Curtains & Blinds",
        }
            # If user enters something unexpected, default to TV Mounting
        label = service_map.get(service, "TV Mounting")
        params.append(f"service_type={label}")

    if params:
        return f"{base_url}?{'&'.join(params)}"
    return base_url


# =====================================================
# MAIN QUOTE FORM (HTML)
# =====================================================
@app.get("/", response_class=HTMLResponse)
def show_form(request: Request):
    return templates.TemplateResponse("quote_form.html", {"request": request})


# =====================================================
# QUOTE (HTML)
# =====================================================
@app.post("/quote-html", response_class=HTMLResponse)
async def quote_html(
    request: Request,
    background_tasks: BackgroundTasks,
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
    contact_email: str = Form(""),
    service: str = Form("tv_mounting"),

    tv_size: int = Form(0),
    wall_type: str = Form("drywall"),
    conceal_type: str = Form("none"),
    soundbar: str = Form("false"),
    led: str = Form("false"),

    shelves: str = Form("false"),
    shelves_install_count: int = Form(0),
    shelves_remove_count: int = Form(0),

    picture_count: int = Form(0),
    picture_large_count: int = Form(0),
    picture_has_large: str = Form("false"),
    gallery_wall: str = Form("false"),

    closet_shelving: str = Form("false"),
    closet_needs_materials: str = Form("false"),
    closet_install_count: int = Form(0),
    closet_remove_count: int = Form(0),
    closet_shelf_not_sure: str = Form("false"),

    decor_count: int = Form(0),

    same_day: str = Form("false"),
    after_hours: str = Form("false"),
    zip_code: str = Form("20735"),
):
    def to_bool(value: str) -> bool:
        return str(value).lower() == "true"

    result = calculate_quote(
        service=service,
        tv_size=tv_size,
        wall_type=wall_type,
        conceal_type=conceal_type,
        soundbar=to_bool(soundbar),
        shelves=to_bool(shelves),
        picture_count=picture_count,
        picture_large_count=picture_large_count if to_bool(picture_has_large) else 0,
        gallery_wall=to_bool(gallery_wall),
        led=to_bool(led),
        same_day=to_bool(same_day),
        after_hours=to_bool(after_hours),
        zip_code=zip_code,
        closet_shelving=to_bool(closet_shelving),
        closet_needs_materials=to_bool(closet_needs_materials),
        decor_count=decor_count,
        shelves_install_count=shelves_install_count,
        shelves_remove_count=shelves_remove_count,
        closet_install_count=closet_install_count,
        closet_remove_count=closet_remove_count,
        closet_shelf_not_sure=to_bool(closet_shelf_not_sure),
    )

    booking_url = build_booking_url(
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        service=service,
    )

    background_tasks.add_task(
        send_lead_to_zapier,
        contact_name,
        contact_phone,
        contact_email,
        service,
        tv_size,
        wall_type,
        conceal_type,
        picture_count,
        to_bool(same_day),
        to_bool(after_hours),
        zip_code,
        booking_url,
        result,
    )

    return templates.TemplateResponse(
        "quote_result.html",
        {
            "request": request,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "contact_email": contact_email,
            "booking_url": booking_url,
            **result,
        },
    )


# =====================================================
# QUOTE (JSON API)
# =====================================================
@app.post("/quote")
def get_quote(request_data: QuoteRequest, background_tasks: BackgroundTasks):
    result = calculate_quote(**request_data.dict())

    booking_url = build_booking_url(
        contact_name=request_data.contact_name or "",
        contact_email=request_data.contact_email or "",
        contact_phone=request_data.contact_phone or "",
        service=request_data.service,
    )

    background_tasks.add_task(
        send_lead_to_zapier,
        request_data.contact_name,
        request_data.contact_phone,
        request_data.contact_email,
        request_data.service,
        request_data.tv_size,
        request_data.wall_type,
        request_data.conceal_type,
        request_data.picture_count,
        request_data.same_day,
        request_data.after_hours,
        request_data.zip_code,
        booking_url,
        result,
    )

    return {
        **result,
        "booking_url": booking_url,
    }

