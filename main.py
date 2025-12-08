from typing import Optional
from datetime import datetime, date, timedelta
import re
import urllib.parse

import requests
import pytz

from fastapi import FastAPI, Request, Form, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from google_calendar import create_booking_event, get_available_slots_for_date
from quote_logic import calculate_quote
from config import TIMEZONE

# =====================================================
# FastAPI app & templates
# =====================================================

app = FastAPI(title="Hawkins Pro Mounting Quote API")

# Serve static files (for logo, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# =====================================================
# QUOTE FORM (HOME PAGE)
# =====================================================
@app.get("/", response_class=HTMLResponse)
async def show_quote_form(request: Request):
    return templates.TemplateResponse(
        "quote_form.html",
        {"request": request},
    )

# =====================================================
# Service + Zapier configuration
# =====================================================

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
AFTER_HOURS_SURCHARGE = 25.0
AFTER_HOURS_START_HOUR = 18

ZAPIER_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/25408903/uz2ihgh/"
BOOKING_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/25408903/ukipdo4/"

CALENDAR_ID = "primary"


# =====================================================
# Helper: compute booking duration (fallback)
# =====================================================
def compute_booking_duration_hours(
    include_tv: bool,
    include_pictures: bool,
    include_shelves: bool,
    include_closet: bool,
    include_decor: bool,
) -> float:
    """
    Fallback if we *don't* get estimated_hours from the quote.
    Very rough: 2–4 hours based on how many service buckets are selected.
    """
    flags = [include_tv, include_pictures, include_shelves, include_closet, include_decor]
    count = sum(1 for f in flags if f)

    if count <= 1:
        return 2.0
    elif count == 2:
        return 3.0
    else:
        return 4.0


# =====================================================
# Address validation helper
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
    hours: Optional[float] = Query(None),

    # service flags from booking_url
    tv: Optional[str] = Query(None),
    pictures: Optional[str] = Query(None),
    shelves: Optional[str] = Query(None),
    closet: Optional[str] = Query(None),
    decor: Optional[str] = Query(None),

    num_services: Optional[int] = Query(None),
):
    def flag(value: Optional[str]) -> bool:
        return str(value).lower() == "true"

    # Build a clean list of service names for this visit
    service_flags = {
        "TV Mounting": flag(tv),
        "Picture & Art Hanging": flag(pictures),
        "Floating Shelves": flag(shelves),
        "Closet Shelving": flag(closet),
        "Curtains & Blinds": flag(decor),
    }
    services_this_visit = [svc for svc, enabled in service_flags.items() if enabled]
    if num_services is None:
        num_services_val = len(services_this_visit) or 1
    else:
        num_services_val = num_services

    prefilled = {
        "service_type": service_type,
        "name": name,
        "email": email,
        "phone": phone,
        "address_street": "",
        "address_city": "",
        "address_state": "",
        "address_zip": "",
        "estimated_hours": hours,
    }

    return templates.TemplateResponse(
        "booking_form.html",
        {
            "request": request,
            "service_types": SERVICE_TYPES,
            "prefilled": prefilled,
            "errors": {},
            "services_this_visit": services_this_visit,
            "num_services": num_services_val,
        },
    )

# =====================================================
# BOOKING FORM (POST)
# =====================================================
# =====================================================
# BOOKING FORM (POST)
# =====================================================
@app.post("/book", response_class=HTMLResponse)
async def submit_booking(
    request: Request,
    background_tasks: BackgroundTasks,

    # Primary service (read-only in the form, but still posted as a hidden field)
    service_type: str = Form(...),

    # New style: ISO datetime string from availability dropdown
    time_slot: Optional[str] = Form(None),

    # Legacy style (kept just in case, if no time_slot)
    appointment_date: Optional[str] = Form(None),
    appointment_time: Optional[str] = Form(None),

    # NEW: services for this visit, passed from booking_form.html
    services_this_visit_raw: str = Form(""),
    num_services: Optional[int] = Form(None),

    # Customer info
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),

    # Separate address fields
    address_street: str = Form(...),
    address_city: str = Form(...),
    address_state: str = Form(...),
    address_zip: str = Form(...),

    # Estimated hours from the quote (hidden input)
    estimated_hours: Optional[float] = Form(None),

    notes: str = Form(""),
):
    tz = pytz.timezone(TIMEZONE)

    # 0) Validate address
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
            "estimated_hours": estimated_hours,
        }
        errors = {"address": addr_error}

        return templates.TemplateResponse(
            "booking_form.html",
            {
                "request": request,
                "service_types": SERVICE_TYPES,
                "prefilled": prefilled,
                "errors": errors,
                "services_this_visit": [],
                "num_services": 1,
            },
            status_code=400,
        )

    # Build full address string
    full_address = (
        f"{parsed_address['street']}, "
        f"{parsed_address['city']}, "
        f"{parsed_address['state']} {parsed_address['zip']}"
    )

    # 1) Determine start datetime
    if time_slot:
        # time_slot is an ISO string from the availability dropdown
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
        # Fallback to separate date / time fields if needed
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

    # Normalize timezone
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    else:
        start_dt = start_dt.astimezone(tz)

    # 2) Parse services list from hidden field
    if services_this_visit_raw:
        services_this_visit = [
            s.strip()
            for s in services_this_visit_raw.split(",")
            if s.strip()
        ]
    else:
        services_this_visit = []

    if num_services is not None and num_services > 0:
        effective_num_services = int(num_services)
    else:
        effective_num_services = len(services_this_visit) or 1

    # 3) Duration: prefer estimated_hours from quote, fallback to 2 hours
    if estimated_hours is not None and estimated_hours > 0:
        duration_hours = float(estimated_hours)
    else:
        duration_hours = 2.0  # basic fallback

    end_dt = start_dt + timedelta(hours=duration_hours)

    # 4) Same-day / after-hours flags (for internal tracking / Zap)
    now_local = datetime.now(tz)
    is_same_day_booking = (start_dt.date() == now_local.date())
    is_after_hours_booking = start_dt.hour >= AFTER_HOURS_START_HOUR

    # 5) Build calendar event details
    summary = f"{service_type} - {name}"

    description_lines = [
        f"Service (primary): {service_type}",
        f"Customer: {name}",
        f"Email: {email}",
        f"Phone: {phone}",
        f"Address: {full_address}",
    ]

    if services_this_visit:
        description_lines.append("Services this visit: " + ", ".join(services_this_visit))

    description_lines.append(f"Number of services: {effective_num_services}")
    description_lines.append(f"Expected duration: {duration_hours:.1f} hours")
    description_lines.append(f"Same-day booking: {'YES' if is_same_day_booking else 'NO'}")
    description_lines.append(f"After-hours booking: {'YES' if is_after_hours_booking else 'NO'}")

    if notes:
        description_lines.append(f"Notes: {notes}")

    description = "\n".join(description_lines)

    # 6) Create the calendar event
    create_booking_event(
        summary=summary,
        description=description,
        start_dt=start_dt,
        end_dt=end_dt,
        customer_email=email,
        calendar_id=CALENDAR_ID,
    )

    # 7) Trigger booking Zap with full service list + duration
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
        effective_num_services,
    )

    # 8) Show confirmation page
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
            "num_services": effective_num_services,
            "duration_hours": duration_hours,

            # Extra fields your template references (safe defaults)
            "tv_size": None,
            "picture_count": None,
            "shelves_count": None,
            "closet_shelf_count": None,
            "decor_count": None,
            "contact_pref": "email",
            "ladder_required": False,
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
    tv_count: int = 0
    wall_type: str = "drywall"
    conceal_type: str = "none"
    soundbar: bool = False
    led: bool = False

    shelves: bool = False
    shelves_count: int = 0
    shelves_remove_count: int = 0

    picture_count: int = 0
    picture_large_count: int = 0

    closet_shelving: bool = False
    closet_needs_materials: bool = False
    closet_shelf_count: int = 0
    closet_shelf_not_sure: bool = False
    closet_remove_count: int = 0

    decor_count: int = 0
    decor_remove_count: int = 0

    same_day: bool = False
    after_hours: bool = False

    ladder_required: bool = False
    parking_notes: str = ""
    preferred_contact: str = ""
    gallery_wall: bool = False

    zip_code: str = "20735"


# =====================================================
# Zapier sending helper for QUOTES
# =====================================================
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
    """
    Send full quote details to Zapier (Zap A).
    This now includes:
      - per-service counts
      - removal counts
      - ladder / gallery flags
      - estimated_hours from the quote
      - line-item dollar amounts
    """
    if not ZAPIER_WEBHOOK_URL:
        print("ZAPIER_WEBHOOK_URL is empty; skipping Zapier send")
        return

    try:
        line_items = (quote_result.get("line_items") or {}) if isinstance(quote_result, dict) else {}

        # High-level counts & flags from quote_result (all .get() so it won't crash if missing)
        tv_count = quote_result.get("tv_count", 0)
        tv_remove_count = quote_result.get("tv_remove_count", 0)

        picture_count_total = quote_result.get("picture_count", 0)
        picture_large_count = quote_result.get("picture_large_count", 0)
        gallery_wall = quote_result.get("gallery_wall", False)

        shelves_count = quote_result.get("shelves_count", 0)
        shelves_remove_count = quote_result.get("shelves_remove_count", 0)

        closet_shelf_count = quote_result.get("closet_shelf_count", 0)
        closet_shelf_remove_count = quote_result.get("closet_shelf_remove_count", 0)

        curtains_count = quote_result.get("curtains_count", 0)
        curtains_remove_count = quote_result.get("curtains_remove_count", 0)

        ladder_required = quote_result.get("ladder_required", False)

        estimated_hours = quote_result.get("estimated_hours", 0.0)

        # Core money fields
        subtotal_before_tax = quote_result.get("subtotal_before_tax", 0.0)
        tax_rate = quote_result.get("tax_rate", 0.0)
        tax_amount = quote_result.get("tax_amount", 0.0)
        estimated_total_with_tax = quote_result.get("estimated_total_with_tax", 0.0)

        tv_total = line_items.get("tv_total", 0.0)
        tv_remove_total = line_items.get("tv_remove_total", 0.0)
        picture_total = line_items.get("picture_total", 0.0)
        picture_large_total = line_items.get("picture_large_total", 0.0)
        shelves_total = line_items.get("shelves_total", 0.0)
        shelves_remove_total = line_items.get("shelves_remove_total", 0.0)
        closet_total = line_items.get("closet_total", 0.0)
        closet_remove_total = line_items.get("closet_remove_total", 0.0)
        curtains_total = line_items.get("curtains_total", 0.0)
        curtains_remove_total = line_items.get("curtains_remove_total", 0.0)
        addons = line_items.get("addons", 0.0)
        multi_service_discount = line_items.get("multi_service_discount", 0.0)
        same_day_surcharge = line_items.get("same_day_surcharge", 0.0)
        after_hours_surcharge = line_items.get("after_hours_surcharge", 0.0)
        total_surcharge = same_day_surcharge + after_hours_surcharge

        payload = {
            # Basic metadata
            "timestamp": datetime.utcnow().isoformat(),
            "contact_name": contact_name or "",
            "contact_phone": contact_phone or "",
            "contact_email": contact_email or "",
            "zip_code": zip_code,
            "service": service,
            "tv_size": tv_size,
            "wall_type": wall_type,
            "conceal_type": conceal_type,
            "items_count": picture_count,  # legacy field you were already using
            "same_day": same_day,
            "after_hours": after_hours,
            "booking_url": booking_url,

            # NEW – counts & flags
            "tv_count": tv_count,
            "tv_remove_count": tv_remove_count,
            "picture_count_total": picture_count_total,
            "picture_large_count": picture_large_count,
            "gallery_wall": gallery_wall,
            "shelves_count": shelves_count,
            "shelves_remove_count": shelves_remove_count,
            "closet_shelf_count": closet_shelf_count,
            "closet_shelf_remove_count": closet_shelf_remove_count,
            "curtains_count": curtains_count,
            "curtains_remove_count": curtains_remove_count,
            "ladder_required": ladder_required,
            "estimated_hours": estimated_hours,

            # NEW – line-item money fields
            "tv_total": tv_total,
            "tv_remove_total": tv_remove_total,
            "picture_total": picture_total,
            "picture_large_total": picture_large_total,
            "shelves_total": shelves_total,
            "shelves_remove_total": shelves_remove_total,
            "closet_total": closet_total,
            "closet_remove_total": closet_remove_total,
            "curtains_total": curtains_total,
            "curtains_remove_total": curtains_remove_total,
            "addons": addons,
            "multi_service_discount": multi_service_discount,
            "same_day_surcharge": same_day_surcharge,
            "after_hours_surcharge": after_hours_surcharge,
            "total_surcharge": total_surcharge,

            # Totals
            "subtotal_before_tax": subtotal_before_tax,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "estimated_total_with_tax": estimated_total_with_tax,
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
) -> None:
    """
    Sends booking details (including duration_hours) to the Booking Zap.
    """
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
            "duration_hours": duration_hours,   # NEW – matches quote’s estimated_hours
            "notes": notes or "",
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
    estimated_hours: Optional[float] = None,
    service_flags: Optional[dict] = None,
):

    """
    Build a booking URL that includes all multi-service flags.
    service_flags = {
        "tv": bool,
        "pictures": bool,
        "shelves": bool,
        "closet": bool,
        "decor": bool
    }
    """

    base_url = "/book"
    params = []

    if contact_name:
        params.append(f"name={contact_name}")
    if contact_email:
        params.append(f"email={contact_email}")
    if contact_phone:
        params.append(f"phone={contact_phone}")

    # Primary service label
    service_map = {
        "tv_mounting": "TV Mounting",
        "picture_hanging": "Picture & Art Hanging",
        "floating_shelves": "Floating Shelves",
        "closet_shelving": "Closet Shelving",
        "decor": "Curtains & Blinds",
        "curtains_blinds": "Curtains & Blinds",
    }
    label = service_map.get(service, "TV Mounting")
    params.append(f"service_type={label}")

    # Estimated hours
    if estimated_hours is not None and estimated_hours > 0:
        params.append(f"hours={estimated_hours:.2f}")

    # Multi-service flags
    if service_flags:
        for key, value in service_flags.items():
            params.append(f"{key}={'true' if value else 'false'}")

    if not params:
        return base_url

    return f"{base_url}?{'&'.join(params)}"

    base_url = "/book"

    # Map internal service code -> friendly label
    service_map = {
        "tv_mounting": "TV Mounting",
        "picture_hanging": "Picture & Art Hanging",
        "floating_shelves": "Floating Shelves",
        "closet_shelving": "Closet Shelving",
        "decor": "Curtains & Blinds",
        "curtains_blinds": "Curtains & Blinds",
    }
    service_label = service_map.get(service, "TV Mounting")

    params = {
        "name": contact_name or "",
        "email": contact_email or "",
        "phone": contact_phone or "",
        "service_type": service_label,
    }

    if estimated_hours is not None and estimated_hours > 0:
        params["hours"] = f"{estimated_hours:.2f}"

    if services_this_visit:
        params["services"] = ", ".join(services_this_visit)

    if num_services is not None and num_services > 0:
        params["num_services"] = str(num_services)

    # Remove empty values
    params = {k: v for k, v in params.items() if v}

    if not params:
        return base_url

    return f"{base_url}?" + urllib.parse.urlencode(params)

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
    tv_count: int = Form(0),
    tv_remove_count: int = Form(0),

    wall_type: str = Form("drywall"),
    conceal_type: str = Form("none"),
    soundbar: str = Form("false"),
    led: str = Form("false"),

    shelves: str = Form("false"),
    shelves_count: int = Form(0),
    shelves_remove_count: int = Form(0),

    picture_count: int = Form(0),
    picture_large_count: int = Form(0),

    closet_shelving: str = Form("false"),
    closet_needs_materials: str = Form("false"),
    closet_shelf_count: int = Form(0),
    closet_shelf_not_sure: str = Form("false"),
    closet_remove_count: int = Form(0),

    decor_count: int = Form(0),
    decor_remove_count: int = Form(0),

    same_day: str = Form("false"),
    after_hours: str = Form("false"),

    ladder_required: str = Form("false"),
    parking_notes: str = Form(""),
    preferred_contact: str = Form(""),
    gallery_wall: str = Form("false"),

    zip_code: str = Form("20735"),
):
    def to_bool(value: str) -> bool:
        return str(value).lower() == "true"

    # ----------------------------------------------------
    # 0) Validate contact info (name + email + phone REQUIRED)
    # ----------------------------------------------------
    name_clean = (contact_name or "").strip()
    email_clean = (contact_email or "").strip()
    phone_digits = re.sub(r"\D", "", contact_phone or "")

    if not name_clean:
        return HTMLResponse(
            "<h3>Error: Your name is required.</h3>"
            "<p>Please go back and enter your name so I know who the quote is for.</p>",
            status_code=400,
        )

    if not email_clean or "@" not in email_clean:
        return HTMLResponse(
            "<h3>Error: A valid email address is required.</h3>"
            "<p>Please go back and enter a valid email so I can send your quote and booking link.</p>",
            status_code=400,
        )

    if not phone_digits or len(phone_digits) < 10:
        return HTMLResponse(
            "<h3>Error: A valid phone number is required.</h3>"
            "<p>Please go back and enter a working phone number so I can confirm your appointment.</p>",
            status_code=400,
        )

    # ----------------------------------------------------
    # 1) Calculate the quote
    # ----------------------------------------------------
    result = calculate_quote(
        service=service,
        tv_size=tv_size,
        tv_count=tv_count,
        wall_type=wall_type,
        conceal_type=conceal_type,
        soundbar=to_bool(soundbar),
        shelves=to_bool(shelves),
        picture_count=picture_count,
        led=to_bool(led),
        same_day=to_bool(same_day),
        after_hours=to_bool(after_hours),
        zip_code=zip_code,
        closet_shelving=to_bool(closet_shelving),
        closet_needs_materials=to_bool(closet_needs_materials),
        decor_count=decor_count,
        shelves_count=shelves_count,
        closet_shelf_count=closet_shelf_count,
        closet_shelf_not_sure=to_bool(closet_shelf_not_sure),
        tv_remove_count=tv_remove_count,
        shelves_remove_count=shelves_remove_count,
        closet_remove_count=closet_remove_count,
        decor_remove_count=decor_remove_count,
        picture_large_count=picture_large_count,
        ladder_required=to_bool(ladder_required),
        parking_notes=parking_notes,
        preferred_contact=preferred_contact,
        gallery_wall=to_bool(gallery_wall),
    )

    # ----------------------------------------------------
    # 2) Detect all services included (for booking)
    # ----------------------------------------------------
    service_flags = {
        "tv": (result.get("tv_count", 0) > 0),
        "pictures": (
            result.get("picture_count", 0) > 0
            or result.get("picture_large_count", 0) > 0
        ),
        "shelves": (result.get("shelves_count", 0) > 0),
        "closet": (result.get("closet_shelf_count", 0) > 0),
        "decor": (result.get("curtains_count", 0) > 0),
    }

    # ----------------------------------------------------
    # 3) Build booking URL (passes multi-service flags)
    # ----------------------------------------------------
    booking_url = build_booking_url(
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        service=service,
        estimated_hours=result.get("estimated_hours"),
        service_flags=service_flags,
    )

    # ----------------------------------------------------
    # 4) Send Zapier lead
    # ----------------------------------------------------
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

    # ----------------------------------------------------
    # 5) Render quote result page
    # ----------------------------------------------------
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

        # Build flags for JSON quote as well
    service_flags = {
        "tv": (result.get("tv_count", 0) > 0),
        "pictures": (
            result.get("picture_count", 0) > 0
            or result.get("picture_large_count", 0) > 0
        ),
        "shelves": (result.get("shelves_count", 0) > 0),
        "closet": (result.get("closet_shelf_count", 0) > 0),
        "decor": (result.get("curtains_count", 0) > 0),
    }

    booking_url = build_booking_url(
        contact_name=request_data.contact_name or "",
        contact_email=request_data.contact_email or "",
        contact_phone=request_data.contact_phone or "",
        service=request_data.service,
        estimated_hours=result.get("estimated_hours"),
        service_flags=service_flags,
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
