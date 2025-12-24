from typing import Optional
from datetime import datetime, date, timedelta
import re
import urllib.parse

import requests
import pytz

import os
import stripe
import uuid

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
# Stripe (Payment Holds)
# =====================================================
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
ADMIN_CAPTURE_TOKEN = os.getenv("ADMIN_CAPTURE_TOKEN", "")
DEPOSIT_AMOUNT_CENTS = 2000  # $20.00

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# =====================================================
# QUOTE FORM (HOME PAGE)
# =====================================================
@app.get("/", response_class=HTMLResponse)
async def show_quote_form(request: Request):
    return templates.TemplateResponse(
        "quote_form.html",
            {"request": request, "build": "LIVE-TEST-2025-12-24-1"},
    )

# =====================================================
# PAYMENT HOLD (STRIPE)
# =====================================================

@app.get("/pay", response_class=HTMLResponse)
async def pay_page(
    request: Request,

    # Booking details
    service_type: str = "",
    time_slot: str = "",
    appointment_date: str = "",
    appointment_time: str = "",

    services_this_visit_raw: str = "",
    num_services: str = "",
    estimated_hours: str = "",

    # Customer details
    name: str = "",
    email: str = "",
    phone: str = "",

    # Address
    address_street: str = "",
    address_city: str = "",
    address_state: str = "",
    address_zip: str = "",

    notes: str = "",
):
    return templates.TemplateResponse(
        "pay_hold.html",
        {
            "request": request,
            "publishable_key": STRIPE_PUBLISHABLE_KEY,
            "amount_dollars": "20.00",

            # Pass-through to template for the POST -> /book
            "service_type": service_type,
            "time_slot": time_slot,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,

            "services_this_visit_raw": services_this_visit_raw,
            "num_services": num_services,
            "estimated_hours": estimated_hours,

            "name": name,
            "email": email,
            "phone": phone,

            "address_street": address_street,
            "address_city": address_city,
            "address_state": address_state,
            "address_zip": address_zip,

            "notes": notes,
        },
    )

@app.post("/api/create-hold-intent")
async def create_hold_intent(payload: dict):
    if not STRIPE_SECRET_KEY:
        return JSONResponse(status_code=500, content={"error": "Stripe not configured"})

    # -------------------------
    # Helper: safe truncation
    # -------------------------
    def trunc(val, max_len: int) -> str:
        s = "" if val is None else str(val)
        s = s.strip()
        return s[:max_len]

    # Pull useful info from the client (safe defaults)
    email = (payload.get("email") or "").strip()
    name = (payload.get("name") or "").strip()
    phone = (payload.get("phone") or "").strip()

    service_type = payload.get("service_type") or ""
    time_slot = payload.get("time_slot") or ""
    appointment_date = payload.get("appointment_date") or ""
    appointment_time = payload.get("appointment_time") or ""

    services_this_visit_raw = payload.get("services_this_visit_raw") or ""
    num_services = payload.get("num_services") or ""
    estimated_hours = payload.get("estimated_hours") or ""

    # NEW: optional fields (keep light)
    address_zip = (payload.get("address_zip") or "").strip()
    notes = payload.get("notes") or ""

    # -------------------------
    # NEW: booking_ref + source + environment
    # -------------------------
    booking_ref = uuid.uuid4().hex[:12]  # short unique ID, e.g. "a1b2c3d4e5f6"
    booking_source = (payload.get("booking_source") or "quote_tool").strip()  # "quote_tool" or "phone"
    environment = (os.getenv("APP_ENV") or "prod").strip()  # "prod" or "test"

    # ----------------------------------------
    # Optional: Create/find a Stripe Customer
    # ----------------------------------------
    customer_id = None
    if email:
        try:
            existing = stripe.Customer.list(email=email, limit=1)
            if existing.data:
                customer_id = existing.data[0].id
            else:
                customer = stripe.Customer.create(
                    email=email,
                    name=name or None,
                    phone=phone or None,
                )
                customer_id = customer.id
        except Exception:
            # Not fatal — still allow payment intent creation
            customer_id = None

    # -------------------------
    # Create PaymentIntent hold
    # -------------------------
    try:
        
        metadata = {
            # NEW - always helpful for linking across systems
            "booking_ref": booking_ref,
            "booking_source": trunc(booking_source, 30),
            "environment": trunc(environment, 10),

            # Customer
            "email": trunc(email, 100),
            "name": trunc(name, 80),
            "phone": trunc(phone, 30),

            # Booking summary
            "service_type": trunc(service_type, 80),
            "time_slot": trunc(time_slot, 80),
            "appointment_date": trunc(appointment_date, 20),
            "appointment_time": trunc(appointment_time, 20),

            # Quote/service bundle
            "services_this_visit_raw": trunc(services_this_visit_raw, 450),
            "num_services": trunc(num_services, 10),
            "estimated_hours": trunc(estimated_hours, 20),

            # Optional light fields
            "address_zip": trunc(address_zip, 10),
            "notes_preview": trunc(notes, 200),
        }

        intent_params = {
            "amount": DEPOSIT_AMOUNT_CENTS,
            "currency": "usd",
            "capture_method": "manual",
            "description": "Hawkins Pro Mounting – Appointment Hold",
            "automatic_payment_methods": {"enabled": True},
            "metadata": metadata,
        }

        if customer_id:
            intent_params["customer"] = customer_id

        intent = stripe.PaymentIntent.create(**intent_params)

        return {
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "booking_ref": booking_ref,  # optional but useful for your own logs/UI
        }

    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to create hold: {str(e)}"})

@app.post("/api/capture-hold")
async def capture_hold(payload: dict):
    token = payload.get("token", "")
    payment_intent_id = payload.get("payment_intent_id", "")

    if token != ADMIN_CAPTURE_TOKEN:
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized"},
        )

    intent = stripe.PaymentIntent.capture(payment_intent_id)
    return {
        "status": intent.status,
        "payment_intent_id": intent.id,
    }

@app.post("/api/cancel-hold")
async def cancel_hold(payload: dict):
    token = payload.get("token", "")
    payment_intent_id = payload.get("payment_intent_id", "")

    if token != ADMIN_CAPTURE_TOKEN:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if not payment_intent_id:
        return JSONResponse(status_code=400, content={"error": "Missing payment_intent_id"})

    try:
        intent = stripe.PaymentIntent.cancel(payment_intent_id)
        return {"status": intent.status, "payment_intent_id": intent.id}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Cancel failed: {str(e)}"})

@app.get("/admin/invoice", response_class=HTMLResponse)
async def admin_invoice_page(
    request: Request,
    token: str = Query(""),
    booking_ref: str = Query(""),
):
    # Option B protection: require ?token=
    if token != ADMIN_CAPTURE_TOKEN:
        return HTMLResponse("Unauthorized", status_code=401)

    return templates.TemplateResponse(
        "admin_invoice.html",
        {
            "request": request,
            "token": token,
            "booking_ref": (booking_ref or "").strip(),
        },
    )

@app.post("/admin/create-invoice")
async def admin_create_invoice(payload: dict):
    # Detect environment (local vs prod)
    environment = (os.getenv("APP_ENV") or "prod").strip()
    force_new_customer = (environment == "local")

    # Protect this endpoint
    token = payload.get("token", "")
    if token != ADMIN_CAPTURE_TOKEN:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    email = (payload.get("email") or "").strip()
    name = (payload.get("name") or "").strip()
    amount_cents = int(payload.get("amount_cents") or 0)
    description = payload.get("description") or "Hawkins Pro Mounting – Service Invoice"
    days_until_due = 0  # always due immediately
    payment_intent_id = (payload.get("payment_intent_id") or "").strip()
    booking_ref = (payload.get("booking_ref") or "").strip()
    booking_source = (payload.get("booking_source") or "manual").strip()
    service_date = (payload.get("service_date") or "").strip()
    address_zip = (payload.get("address_zip") or "").strip()
    booking_ref = (payload.get("booking_ref") or "").strip()
    if not booking_ref:
        booking_ref = f"HPM-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    invoice_metadata = {
        "booking_ref": booking_ref,
        "booking_source": booking_source,
        "service_date": service_date,
        "address_zip": address_zip,
    }

    if not email or amount_cents <= 0:
        return JSONResponse(status_code=400, content={"error": "Missing email or amount_cents"})

    # 1) Find or create customer
    if force_new_customer:
        # Local testing: always create a fresh customer (avoids paid invoices)
        customer = stripe.Customer.create(email=email, name=name or None)
    else:
        existing = stripe.Customer.list(email=email, limit=1)
        if existing.data:
            customer = existing.data[0]
        else:
            customer = stripe.Customer.create(email=email, name=name or None)

    print("INVOICE ITEM DEBUG:", {
        "email": email,
        "customer_id": customer.id if customer else None,
        "amount_cents": amount_cents,
        "description": description,
        "booking_ref": booking_ref,
        "booking_source": booking_source,
    })

    # 2) Create an invoice item (line item)
    stripe.InvoiceItem.create(
        customer=customer.id,
        amount=amount_cents,
        currency="usd",
        description=description,
        metadata=invoice_metadata,
    )

    # 3) Create invoice and send it
    invoice = stripe.Invoice.create(
        customer=customer.id,
        collection_method="send_invoice",
        days_until_due=days_until_due,
        auto_advance=True,
        pending_invoice_items_behavior="include",
        metadata=invoice_metadata,
    )

    # Finalize now (hosted URL + PDF become available)
    invoice = stripe.Invoice.finalize_invoice(invoice.id)

    invoice = stripe.Invoice.retrieve(invoice.id)
    print("INVOICE DEBUG (after finalize):", {
        "id": invoice.id,
        "status": getattr(invoice, "status", None),
        "total": getattr(invoice, "total", None),
        "amount_due": getattr(invoice, "amount_due", None),
        "amount_remaining": getattr(invoice, "amount_remaining", None),
        "paid": getattr(invoice, "paid", None),
    })

    if environment != "local":
        invoice = stripe.Invoice.send_invoice(invoice.id)

    invoice = stripe.Invoice.retrieve(invoice.id)
    print("INVOICE DEBUG (after send):", {
        "id": invoice.id,
        "status": getattr(invoice, "status", None),
        "total": getattr(invoice, "total", None),
        "amount_due": getattr(invoice, "amount_due", None),
        "amount_remaining": getattr(invoice, "amount_remaining", None),
        "paid": getattr(invoice, "paid", None),
    })

    print("INVOICE DEBUG:", {
        "id": invoice.id,
        "status": getattr(invoice, "status", None),
        "total": getattr(invoice, "total", None),
        "amount_due": getattr(invoice, "amount_due", None),
        "amount_remaining": getattr(invoice, "amount_remaining", None),
        "paid": getattr(invoice, "paid", None),
    })

    # 4) Cancel/release the $20 hold (recommended)
    hold_cancel_status = None
    if payment_intent_id:
        try:
            canceled = stripe.PaymentIntent.cancel(payment_intent_id)
            hold_cancel_status = canceled.status
        except Exception as e:
            hold_cancel_status = f"cancel_failed: {str(e)}"

    return {
        "status": "created",
        "invoice_id": invoice.id,
        "invoice_status": invoice.status,
        "paid": getattr(invoice, "paid", None),
        "amount_due": getattr(invoice, "amount_due", None),
        "amount_paid": getattr(invoice, "amount_paid", None),
        "amount_remaining": getattr(invoice, "amount_remaining", None),
        "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
        "invoice_pdf": getattr(invoice, "invoice_pdf", None),
        "hold_cancel_status": hold_cancel_status,
    }

@app.post("/admin/mark-invoice-paid")
async def admin_mark_invoice_paid(payload: dict):
    token = payload.get("token", "")
    invoice_id = (payload.get("invoice_id") or "").strip()

    if token != ADMIN_CAPTURE_TOKEN:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if not invoice_id:
        return JSONResponse(status_code=400, content={"error": "Missing invoice_id"})

    try:
        # Mark as paid out-of-band (cash/zelle/etc.)
        invoice = stripe.Invoice.pay(invoice_id, paid_out_of_band=True)
        return {"status": invoice.status, "invoice_id": invoice.id}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Mark paid failed: {str(e)}"})

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, token: str = ""):
    # Simple protection: require token in querystring
    if token != ADMIN_CAPTURE_TOKEN:
        return HTMLResponse("<h3>Unauthorized</h3>", status_code=401)

    return templates.TemplateResponse(
        "admin_invoice.html",
        {
            "request": request,
            "token": token,
        },
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

    payment_intent_id: str = Form(""),
):
    tz = pytz.timezone(TIMEZONE)

    if not payment_intent_id:
        return templates.TemplateResponse(
            "booking_error.html",
            {
                "request": request,
                "message": "Payment hold missing. Please start from the payment page to place your $20 appointment hold.",
            },
            status_code=400,
        )

    # ----------------------------------------------------
# 0) Verify Stripe hold (must succeed before booking)
# ----------------------------------------------------
    if not STRIPE_SECRET_KEY:
        return templates.TemplateResponse(
            "booking_error.html",
            {"request": request, "message": "Payment system not configured. Please contact us."},
            status_code=500,
        )

    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        booking_ref = (intent.metadata or {}).get("booking_ref", "")
    except Exception:
        return templates.TemplateResponse(
            "booking_error.html",
            {"request": request, "message": "Could not verify payment hold. Please try again."},
            status_code=400,
        )

    # For manual capture, a successful authorization will usually be "requires_capture"
    if intent.status not in ("requires_capture", "succeeded"):
        return templates.TemplateResponse(
            "booking_error.html",
            {"request": request, "message": "Your $20 hold was not completed. Please try again."},
            status_code=400,
        )

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
        booking_ref,
        payment_intent_id,
    )

    # 8) Show confirmation page
    hold_already_authorized = intent.status in ("requires_capture", "succeeded")

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

        # ✅ NEW: used by booking_confirm.html to hide the hold button
        "hold_already_authorized": hold_already_authorized,

        # ✅ NEW: pass-through fields so the /pay link can be fully pre-filled
        "email": email,
        "phone": phone,
        "notes": notes,

        "address_street": parsed_address.get("street", ""),
        "address_city": parsed_address.get("city", ""),
        "address_state": parsed_address.get("state", ""),
        "address_zip": parsed_address.get("zip", ""),

        # Optional: if you want the /pay page to still receive these in query params
        # (it won't hurt to include them)
        "time_slot": time_slot or start_dt.isoformat(),
        "appointment_date": appointment_date or start_dt.strftime("%Y-%m-%d"),
        "appointment_time": appointment_time or start_dt.strftime("%H:%M"),
        "booking_ref": booking_ref,

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
    booking_ref: str = "",
    payment_intent_id: str = "",
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
            "booking_ref": booking_ref or "",
            "payment_intent_id": payment_intent_id or "",
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
    Build a booking URL that includes:
      - name/email/phone
      - primary service label
      - estimated hours
      - multi-service flags (tv/pictures/shelves/closet/decor)
    """

    base_url = "/book"

    params = []
    if contact_name:
        params.append(("name", contact_name))
    if contact_email:
        params.append(("email", contact_email))
    if contact_phone:
        params.append(("phone", contact_phone))

    service_map = {
        "tv_mounting": "TV Mounting",
        "picture_hanging": "Picture & Art Hanging",
        "floating_shelves": "Floating Shelves",
        "closet_shelving": "Closet Shelving",
        "decor": "Curtains & Blinds",
        "curtains_blinds": "Curtains & Blinds",
    }
    label = service_map.get(service, "TV Mounting")
    params.append(("service_type", label))

    if estimated_hours is not None and estimated_hours > 0:
        params.append(("hours", f"{estimated_hours:.2f}"))

    if service_flags:
        for key, value in service_flags.items():
            params.append((key, "true" if value else "false"))

    query = urllib.parse.urlencode(params)
    return f"{base_url}?{query}" if query else base_url

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
    # NEW: read tv_sizes[] from the posted form (one input per TV)
    form = await request.form()

    # Pull tv_count directly from the posted form
    try:
        tv_count_val = int(str(form.get("tv_count") or "0").strip() or "0")
    except ValueError:
        tv_count_val = 0

    tv_sizes_raw = form.getlist("tv_sizes")

    # Normalize tv_sizes into ints (keep 0s so we can validate “missing” inputs)
    tv_sizes_all = []
    for s in tv_sizes_raw:
        try:
            tv_sizes_all.append(int(str(s).strip() or "0"))
        except ValueError:
            tv_sizes_all.append(0)

    # ✅ Server-side validation:
    # If user says they have TVs (>0), they MUST provide that many sizes and none can be 0/blank
    if tv_count_val > 0:
        if len(tv_sizes_all) != tv_count_val or any(v <= 0 for v in tv_sizes_all):
            # Preserve everything they typed
            prefilled = {k: form.get(k, "") for k in form.keys()}
            # Preserve tv_sizes too (as a CSV so the client can refill the dynamic inputs)
            tv_sizes_csv = ",".join(str(v) for v in tv_sizes_all)

            return templates.TemplateResponse(
                "quote_form.html",
                {
                    "request": request,
                    "errors": {"tv_sizes": "Please enter a size for each TV."},
                    "prefilled": prefilled,
                    "tv_sizes_csv": tv_sizes_csv,
                },
                status_code=400,
            )

    # ✅ Use the validated list (filter to >0 now)
    tv_sizes = [v for v in tv_sizes_all if v > 0]

    # Keep tv_count consistent
    tv_count = tv_count_val

    result = calculate_quote(
        service=service,
        tv_size=tv_size,
        tv_count=tv_count,
        tv_sizes=tv_sizes,
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
