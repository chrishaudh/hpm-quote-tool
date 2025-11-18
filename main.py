from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from quote_logic import calculate_quote

app = FastAPI(title="Hawkins Pro Mounting Quote API")

templates = Jinja2Templates(directory="templates")


class QuoteRequest(BaseModel):
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


@app.get("/", response_class=HTMLResponse)
def show_form(request: Request):
    return templates.TemplateResponse("quote_form.html", {"request": request})


@app.post("/quote-html", response_class=HTMLResponse)
async def quote_html(
    request: Request,
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

    return templates.TemplateResponse(
        "quote_result.html",
        {
            "request": request,
            **result,
        },
    )


@app.post("/quote")
def get_quote(request_data: QuoteRequest):
    return calculate_quote(**request_data.dict())
