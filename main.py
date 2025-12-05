from typing import Optional
from datetime import datetime, date, timedelta
import re

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
):
    """
    Render the booking form.

    - service_type, name, email, phone, hours are normally passed from the quote's
      "Book This Install" button as query params.
    - booking is a simple dict so booking_form.html can safely do things like
      {% if booking.has_tv %} without blowing up on first load.
    """

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

    # Default booking context – safe for template to read
    booking = {
        "has_tv": False,
        "tv_count": 0,
        "tv_remove_count": 0,
        "picture_count": 0,
        "picture_large_flag": False,
        "picture_large_count": 0,
        "gallery_wall": False,
        "has_shelves": False,
        "shelves_count": 0,
        "shelves_remove_count": 0,
        "closet_shelving": False,
        "closet_shelf_count": 0,
        "closet_remove_count": 0,
        "curtains_count": 0,
        "curtains_remove_count": 0,
        "ladder_required": False,
        "estimated_hours": hours,
    }

    return templates.TemplateResponse(
        "booking_form.html",
        {
            "request": request,
            "service_types": SERVICE_TYPES,
            "prefilled": prefilled,
            "errors": {},
            "booking": booking,
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

    # Multi-service selections for this visit (may or may not be present in form)
    include_tv: str = Form("false"),
    include_pictures: str = Form("false"),
    include_shelves: str = Form("false"),
    include_closet: str = Form("false"),
    include_decor: str = Form("false"),

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

    def to_bool(value: str) -> bool:
        return str(value).lower() == "true"

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
            },
            status_code=400,
        )

    # Build full address string
    full_address = f"{parsed_address['street']}, {parsed_address['city']}, {parsed_address['state']} {parsed_address['zip']}"

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

    # 1b) Multi-service flags (fallback if we ever need them)
    include_tv_bool = to_bool(include_tv)
    include_pictures_bool = to_bool(include_pictures)
    include_shelves_bool = to_bool(include_shelves)
    include_closet_bool = to_bool(include_closet)
    include_decor_bool = to_bool(include_decor)

    # 2) Duration: prefer estimated_hours from quote, fall back to rough logic
    if estimated_hours is not None and estimated_hours > 0:
        duration_hours = float(estimated_hours)
    else:
        duration_hours = compute_booking_duration_hours(
            include_tv_bool,
            include_pictures_bool,
            include_shelves_bool,
            include_closet_bool,
            include_decor_bool,
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
    if notes:
        description_lines.append(f"Notes: {notes}")

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
        services_this_visit.append("Curtains & Blinds / Decor")

    num_services = len(services_this_visit) if services_this_visit else 1

    if services_this_visit:
        description_lines.append("Services this visit: " + ", ".join(services_this_visit))

    description_lines.append(f"Number of services: {num_services}")
    description_lines.append(f"Expected duration: {duration_hours:.1f} hours")
    description_lines.append(f"Same-day booking: {'YES' if is_same_day_booking else 'NO'}")
    description_lines.append(f"After-hours booking: {'YES' if is_after_hours_booking else 'NO'}")

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

    # 5) Trigger email confirmation via Zapier (booking Zap)
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
            "base_mounting": line_items.get("tv_total", 0),
            "addons": 0,
            "multi_service_discount": line_items.get("multi_service_discount", 0),
            "tax_rate": quote_result.get("tax_rate", 0),
            "subtotal_before_tax": quote_result.get("subtotal_before_tax", 0),
            "estimated_total_with_tax": quote_result.get("estimated_total_with_tax", 0),
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
            "curtains_blinds": "Curtains & Blinds",
        }
        label = service_map.get(service, "TV Mounting")
        params.append(f"service_type={label}")

    if estimated_hours is not None and estimated_hours > 0:
        params.append(f"hours={estimated_hours:.2f}")

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

    booking_url = build_booking_url(
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        service=service,
        estimated_hours=result.get("estimated_hours"),
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
        estimated_hours=result.get("estimated_hours"),
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
