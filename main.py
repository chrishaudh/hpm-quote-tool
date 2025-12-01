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

# Service types used in both GET /book and POST /book
SERVICE_TYPES = [
    "TV Mounting",
    "Picture & Art Hanging",
    "Floating Shelves",
    "Curtains & Blinds",
    "Closet Shelving",
]

# States you serve (for basic service-area validation)
SERVICE_AREA_STATES = {"DC", "MD", "VA"}

# Simple ZIP pattern: 5 digits or ZIP+4
ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")

# Your Zapier webhook for QUOTES (already in use)
ZAPIER_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/25408903/uz2ihgh/"

# Your Zapier webhook for BOOKINGS (email confirmations)
# Make sure to replace this with your real booking Zap URL.
BOOKING_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/25408903/BOOKING_WEBHOOK_REPLACE_ME/"

# Calendar ID (you can change this later if you use a non-primary calendar)
CALENDAR_ID = "primary"


# =====================================================
# Address validation helper
# =====================================================

def validate_address(address: str) -> tuple[bool, str]:
    """
    Basic address validation.

    Rules:
    - Not empty
    - At least 3 comma-separated parts: street, city, state+ZIP
    - State: 2-letter code (e.g. DC, MD, VA)
    - ZIP: 5 digits (or ZIP+4)
    - Optional: state must be in SERVICE_AREA_STATES
    """
    addr = (address or "").strip()
    if not addr:
        return False, "Please enter the installation address."

    # Expect something like:
    # "123 Main St, Washington, DC 20001"
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) < 3:
        return (
            False,
            "Please include street, city, state, and ZIP code (e.g. 123 Main St, Washington, DC 20001).",
        )

    street = parts[0]
    city = parts[1]
    state_zip = parts[-1]

    # State + ZIP are usually in the last chunk
    tokens = state_zip.split()
    if len(tokens) < 2:
        return (
            False,
            "Please include a 2-letter state and 5-digit ZIP code (e.g. DC 20001).",
        )

    state = tokens[0].upper()
    zip_code = tokens[-1]

    # State: must be 2 letters
    if len(state) != 2 or not state.isalpha():
        return (
            False,
            "State should be a 2-letter abbreviation (e.g. DC, MD, VA).",
        )

    # ZIP: must match pattern
    if not ZIP_RE.match(zip_code):
        return (
            False,
            "ZIP code should be 5 digits (e.g. 20001).",
        )

    # Optional: enforce service area
    if SERVICE_AREA_STATES and state not in SERVICE_AREA_STATES:
        return (
            False,
            "This address appears to be outside our service area (DC, Maryland, Northern Virginia). Please double-check or contact us.",
        )

    # Passed all checks
    return True, ""


# =====================================================
# TEST ROUTE: simple test to confirm calendar booking
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
    """
    Return available time slots for a given date.
    This uses your Google Calendar free/busy + business rules.
    """
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
    address: Optional[str] = Query(None),
):
    """
    Booking form is ONLY intended to be accessed after someone completes
    a quote. The quote tool sends service_type, name, email, phone, address
    as query parameters. We pre-fill the booking form with those values.

    The HTML template (booking_form.html) should:
      - have a date input (name="appointment_date", id="appointment_date")
      - have a time select (name="time_slot", id="time_slot")
      - use JS to call /api/availability when the date changes
    """

    prefilled = {
        "service_type": service_type,
        "name": name,
        "email": email,
        "phone": phone,
        "address": address,
    }

    return templates.TemplateResponse(
        "booking_form.html",
        {
            "request": request,
            "service_types": SERVICE_TYPES,
            "prefilled": prefilled,
            "errors": {},  # no errors on initial load
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
    # NEW STYLE: ISO datetime string from availability dropdown
    time_slot: Optional[str] = Form(None),
    # LEGACY STYLE: separate date + time fields (kept for backward compatibility)
    appointment_date: Optional[str] = Form(None),   # yyyy-mm-dd
    appointment_time: Optional[str] = Form(None),   # HH:MM
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
    notes: str = Form(""),
):
    """
    Handles booking submissions.

    Preferred (new) behavior:
      - Front-end uses /api/availability to fetch slots
      - <select name="time_slot"> holds ISO datetime strings

    Fallback (legacy) behavior:
      - If time_slot is empty, we fall back to appointment_date + appointment_time
    """
    tz = pytz.timezone(TIMEZONE)

    # -----------------------------
    # 0) Validate address
    # -----------------------------
    is_valid_address, addr_error = validate_address(address)
    if not is_valid_address:
        # Re-render the booking form with an inline error under the address field
        prefilled = {
            "service_type": service_type,
            "name": name,
            "email": email,
            "phone": phone,
            "address": address,
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

    # -----------------------------
    # 1) Determine start datetime
    # -----------------------------
    if time_slot:
        # New style: time_slot is an ISO datetime string
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
        # Legacy fallback: use separate date + time
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

    # Ensure start_dt is timezone-aware (America/New_York)
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    else:
        start_dt = start_dt.astimezone(tz)

    # -----------------------------
    # 2) Determine end datetime
    # -----------------------------
    # You can tweak this per service later; for now assume 2-hour slot
    end_dt = start_dt + timedelta(hours=2)

    # -----------------------------
    # 3) Build calendar event details
    # -----------------------------
    summary = f"{service_type} - {name}"
    description_lines = [
        f"Service: {service_type}",
        f"Customer: {name}",
        f"Email: {email}",
        f"Phone: {phone}",
        f"Address: {address}",
    ]
    if notes:
        description_lines.append(f"Notes: {notes}")
    description = "\n".join(description_lines)

    # -----------------------------
    # 4) Create the calendar event 🎫
    # -----------------------------
    create_booking_event(
        summary=summary,
        description=description,
        start_dt=start_dt,
        end_dt=end_dt,
        customer_email=email,
        calendar_id=CALENDAR_ID,
    )

    # -----------------------------
    # 5) Trigger email confirmation via Zapier (non-blocking)
    # -----------------------------
    background_tasks.add_task(
        send_booking_to_zapier,
        name,
        email,
        phone,
        address,
        service_type,
        start_dt,
        end_dt,
        notes,
    )

    # -----------------------------
    # 6) Show confirmation page
    # -----------------------------
    return templates.TemplateResponse(
        "booking_confirm.html",
        {
            "request": request,
            "name": name,
            "service_type": service_type,
            "start": start_dt,
            "end": end_dt,
            "address": address,
        },
    )


# =====================================================
# Pydantic model for JSON quote requests
# =====================================================
class QuoteRequest(BaseModel):
    # Contact info (for JSON API; HTML form uses same field names)
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None

    # Service details
    service: str = "tv_mounting"
    tv_size: int = 0
    wall_type: str = "drywall"
    conceal_type: str = "none"
    soundbar: bool = False
    shelves: bool = False
    picture_count: int = 0
    led: bool = False
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
    """
    Send lead + quote data to Zapier for logging in Google Sheets and triggering
    email flows. Failures here should NEVER break the user experience.
    """

    if not ZAPIER_WEBHOOK_URL:
        print("ZAPIER_WEBHOOK_URL is empty; skipping Zapier send")
        return

    try:
        line_items = quote_result.get("line_items", {}) or {}

        payload = {
            "timestamp": datetime.utcnow().isoformat(),

            # Contact info
            "contact_name": contact_name or "",
            "contact_phone": contact_phone or "",
            "contact_email": contact_email or "",
            "zip_code": zip_code,

            # Job details
            "service": service,
            "tv_size": tv_size,
            "wall_type": wall_type,
            "conceal_type": conceal_type,
            "items_count": picture_count,
            "same_day": same_day,
            "after_hours": after_hours,

            # Booking
            "booking_url": booking_url,

            # Quote breakdown
            "base_mounting": line_items.get("base_mounting", 0),
            "wall_type_adjustment": line_items.get("wall_type_adjustment", 0),
            "wire_concealment": line_items.get("wire_concealment", 0),
            "addons": line_items.get("addons", 0),
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
# Zapier sending helper for BOOKINGS (email confirmation)
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
) -> None:
    """
    Send booking details to Zapier so it can:
      - send a confirmation email
      - log the booking if desired

    This runs in the background and should never break the user experience.
    """
    if not BOOKING_WEBHOOK_URL or "BOOKING_WEBHOOK_REPLACE_ME" in BOOKING_WEBHOOK_URL:
        print("BOOKING_WEBHOOK_URL is empty or placeholder; skipping booking Zapier send")
        return

    try:
        tz = pytz.timezone(TIMEZONE)
        start_local = start_dt.astimezone(tz)
        end_local = end_dt.astimezone(tz)
        now_local = datetime.now(tz)

        is_same_day = (start_local.date() == now_local.date())
        is_after_hours = start_local.hour >= 18  # simple rule; can be refined

        payload = {
            "timestamp": datetime.utcnow().isoformat(),

            # Customer info
            "name": name,
            "email": email,
            "phone": phone,
            "address": address,

            # Booking details
            "service_type": service_type,
            "start_iso": start_local.isoformat(),
            "end_iso": end_local.isoformat(),
            "start_pretty_date": start_local.strftime("%A, %B %-d, %Y"),
            "start_pretty_time": start_local.strftime("%-I:%M %p"),
            "end_pretty_time": end_local.strftime("%-I:%M %p"),
            "is_same_day": is_same_day,
            "is_after_hours": is_after_hours,

            # Extra notes
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
# Build booking URL helper (currently points to Calendly)
# =====================================================
def build_booking_url(
    contact_name: str,
    contact_email: str,
    contact_phone: str,
    service: str,
) -> str:
    """
    Simple helper to create a booking link.
    Replace this with your real scheduling URL (Calendly, etc.)
    """
    base_url = "https://calendly.com/hawkins-pro-mounting/quote"

    params = []
    if contact_name:
        params.append(f"name={contact_name}")
    if contact_email:
        params.append(f"email={contact_email}")
    if contact_phone:
        params.append(f"phone={contact_phone}")
    if service:
        params.append(f"service={service}")

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
# QUOTE (HTML) – called by your front-end form
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
    shelves: str = Form("false"),
    picture_count: int = Form(0),
    led: str = Form("false"),
    same_day: str = Form("false"),
    after_hours: str = Form("false"),
    zip_code: str = Form("20735"),
):
    def to_bool(value: str) -> bool:
        return str(value).lower() == "true"

    # 1) Calculate the quote
    result = calculate_quote(
        service=service,
        tv_size=tv_size,
        wall_type=wall_type,
        conceal_type=conceal_type,
        soundbar=to_bool(soundbar),
        shelves=to_bool(shelves),
        picture_count=picture_count,
        led=to_bool(led),
        same_day=to_bool(same_day),
        after_hours=to_bool(after_hours),
        zip_code=zip_code,
    )

    # 2) Build booking URL for this quote
    booking_url = build_booking_url(
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        service=service,
    )

    # 3) Schedule Zapier send in the background (non-blocking)
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

    # 4) Render HTML result page
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
    """
    JSON API version of the quote endpoint.
    This also logs to Zapier in the background.
    """
    # 1) Calculate the quote
    result = calculate_quote(**request_data.dict())

    # 2) Build booking URL (may be blank if contact info is missing)
    booking_url = build_booking_url(
        contact_name=request_data.contact_name or "",
        contact_email=request_data.contact_email or "",
        contact_phone=request_data.contact_phone or "",
        service=request_data.service,
    )

    # 3) Schedule Zapier send in the background
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

    # 4) Return JSON with quote + booking URL
    return {
        **result,
        "booking_url": booking_url,
    }
