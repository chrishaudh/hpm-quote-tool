from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from datetime import datetime

from quote_logic import calculate_quote

# Try to import requests safely so the app doesn't crash if it's missing
try:
    import requests
except ImportError:
    requests = None

app = FastAPI(title="Hawkins Pro Mounting Quote API")

templates = Jinja2Templates(directory="templates")

# TODO: paste your real Zapier webhook URL here when ready
ZAPIER_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/YOUR/WEBHOOK/URL"


class QuoteRequest(BaseModel):
    # Contact info (for JSON API; HTML form uses same field names)
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None

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
    contact_name: str,
    contact_phone: str,
    service: str,
    tv_size: int,
    wall_type: str,
    conceal_type: str,
    picture_count: int,
    same_day: bool,
    after_hours: bool,
    zip_code: str,
    quote_result: dict,
) -> None:
    """
    Fire-and-forget: send lead + quote data to Zapier for logging in Google Sheets.
    Failures here should NEVER break the user experience.
    """
    # If we don't have requests or webhook not configured, just skip
    if requests is None:
        return
    if not ZAPIER_WEBHOOK_URL or "YOUR/WEBHOOK/URL" in ZAPIER_WEBHOOK_URL:
        return

    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "zip_code": zip_code,
        "service": service,
        "tv_size": tv_size,
        "wall_type": wall_type,
        "conceal_type": conceal_type,
        "items_count": picture_count,
        "same_day": same_day,
        "after_hours": after_hours,
        "base_mounting": quote_result["line_items"]["base_mounting"],
        "wall_type_adjustment": quote_result["line_items"]["wall_type_adjustment"],
        "wire_concealment": quote_result["line_items"]["wire_concealment"],
        "addons": quote_result["line_items"]["addons"],
        "multi_service_discount": quote_result["line_items"]["multi_service_discount"],
        "tax_rate": quote_result["tax_rate"],
        "subtotal_before_tax": quote_result["subtotal_before_tax"],
        "estimated_total_with_tax": quote_result["estimated_total_with_tax"],
    }

    try:
        requests.post(ZAPIER_WEBHOOK_URL, json=payload, timeout=3)
    except Exception:
        # Don't let Zapier failures ever break the app
        pass


@app.get("/", response_class=HTMLResponse)
def show_form(request: Request):
    return templates.TemplateResponse("quote_form.html", {"request": request})


@app.post("/quote-html", response_class=HTMLResponse)
async def quote_html(
    request: Request,
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
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

    # Fire-and-forget lead logging (won't break if it fails)
    send_lead_to_zapier(
        contact_name=contact_name,
        contact_phone=contact_phone,
        service=service,
        tv_size=tv_size,
        wall_type=wall_type,
        conceal_type=conceal_type,
        picture_count=picture_count,
        same_day=to_bool(same_day),
        after_hours=to_bool(after_hours),
        zip_code=zip_code,
        quote_result=result,
    )

    return templates.TemplateResponse(
        "quote_result.html",
        {
            "request": request,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            **result,
        },
    )


@app.post("/quote")
def get_quote(request_data: QuoteRequest):
    return calculate_quote(**request_data.dict())
