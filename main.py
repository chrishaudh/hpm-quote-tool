from typing import Optional
from datetime import datetime

from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from google_calendar import create_booking_event


import requests  # make sure this is in requirements.txt

from quote_logic import calculate_quote

app = FastAPI(title="Hawkins Pro Mounting Quote API")

from datetime import datetime, timedelta

@app.get("/test-book")
async def test_book():
    start = datetime.now() + timedelta(hours=1)
    end = start + timedelta(hours=2)

    create_booking_event(
        summary="Test Booking",
        description="This is a test booking from FastAPI.",
        start_dt=start,
        end_dt=end,
        customer_email=None,
    )

    return {"status": "ok"}

from fastapi import Query

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
    """

    service_types = [
        "TV Mounting",
        "Picture & Art Hanging",
        "Floating Shelves",
        "Curtains & Blinds",
        "Closet Shelving",
    ]

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
            "service_types": service_types,
            "prefilled": prefilled,
        },
    )


@app.post("/book", response_class=HTMLResponse)
async def submit_booking(
    request: Request,
    service_type: str = Form(...),
    appointment_date: str = Form(...),   # yyyy-mm-dd
    appointment_time: str = Form(...),   # HH:MM
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
    notes: str = Form(""),
):
    # Turn date + time strings into a datetime
    start_dt = datetime.fromisoformat(f"{appointment_date}T{appointment_time}")

    # You can tweak this per service later; for now assume 2-hour slot
    end_dt = start_dt + timedelta(hours=2)

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

    # Create the calendar event 🎫
    create_booking_event(
        summary=summary,
        description=description,
        start_dt=start_dt,
        end_dt=end_dt,
        customer_email=email,
        calendar_id="primary",
    )

    # Show confirmation page
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

templates = Jinja2Templates(directory="templates")

# Your real Zapier webhook URL
ZAPIER_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/25408903/uz2ihgh/"


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
    email/SMS flows. Failures here should NEVER break the user experience.
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

            # Quote breakdown (defensive .get's so missing keys don't crash)
            "base_mounting": line_items.get("base_mounting", 0),
            "wall_type_adjustment": line_items.get("wall_type_adjustment", 0),
            "wire_concealment": line_items.get("wire_concealment", 0),
            "addons": line_items.get("addons", 0),
            "multi_service_discount": line_items.get("multi_service_discount", 0),
            "tax_rate": quote_result.get("tax_rate", 0),
            "subtotal_before_tax": quote_result.get("subtotal_before_tax", 0),
            "estimated_total_with_tax": quote_result.get("estimated_total_with_tax", 0),
        }

        # Debug: see exactly what we send to Zapier
        print("📤 Payload sending to Zapier:")
        print(payload)

        resp = requests.post(ZAPIER_WEBHOOK_URL, json=payload, timeout=5)
        resp.raise_for_status()
        print("✅ Lead sent to Zapier successfully")

    except Exception as e:
        # Log the error but don't crash the app
        print(f"❌ Error sending lead to Zapier: {e}")


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

    # Basic query string (you can remove this if you don't care about pre-fill)
    # NOTE: not fully URL-encoded on purpose to keep it simple
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


@app.get("/", response_class=HTMLResponse)
def show_form(request: Request):
    return templates.TemplateResponse("quote_form.html", {"request": request})


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
