from datetime import datetime, timedelta, date, time as dtime
from typing import Optional, List, Tuple, Dict, Any
import os
import json

import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import (
    TIMEZONE,
    BUSINESS_HOURS,
    DEFAULT_JOB_DURATION_MIN,
    DEFAULT_BUFFER_MIN,
    BLACKOUT_DATES,
)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    """
    Load Calendar API credentials.

    - Locally: from token.json (file created by gcal_auth.py)
    - On Render: from GOOGLE_CALENDAR_TOKEN_JSON env var
    """
    token_env = os.getenv("GOOGLE_CALENDAR_TOKEN_JSON")

    if token_env:
        # Read token JSON from environment variable (Render)
        data = json.loads(token_env)
        creds = Credentials.from_authorized_user_info(data, SCOPES)
    else:
        # Local fallback: use token.json file
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    service = build("calendar", "v3", credentials=creds)
    return service


def create_booking_event(
    summary: str,
    description: str,
    start_dt: datetime,
    end_dt: datetime,
    customer_email: Optional[str] = None,
    calendar_id: str = "primary",
):
    """
    Create a Google Calendar event for a booking.
    """
    service = get_calendar_service()

    event_body = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
    }

    if customer_email:
        event_body["attendees"] = [{"email": customer_email}]

    event = service.events().insert(
        calendarId=calendar_id,
        body=event_body,
        sendUpdates="all",
    ).execute()

    return event


# =========================
# Availability / Freebusy
# =========================

def get_busy_intervals(
    calendar_id: str,
    start: datetime,
    end: datetime,
) -> List[Tuple[datetime, datetime]]:
    """
    Returns busy intervals between start and end in local timezone.
    Uses the Google Calendar Freebusy API.
    """
    service = get_calendar_service()
    tz = pytz.timezone(TIMEZONE)

    # Ensure datetimes are timezone-aware
    if start.tzinfo is None:
        start = tz.localize(start)
    else:
        start = start.astimezone(tz)

    if end.tzinfo is None:
        end = tz.localize(end)
    else:
        end = end.astimezone(tz)

    body = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "timeZone": TIMEZONE,
        "items": [{"id": calendar_id}],
    }

    resp = service.freebusy().query(body=body).execute()
    busy = resp["calendars"][calendar_id]["busy"]

    intervals: List[Tuple[datetime, datetime]] = []
    for b in busy:
        start_dt = datetime.fromisoformat(b["start"])
        end_dt = datetime.fromisoformat(b["end"])
        intervals.append((start_dt, end_dt))

    return intervals


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """
    Return True if time interval A overlaps time interval B.
    """
    return max(a_start, b_start) < min(a_end, b_end)


def get_available_slots_for_date(
    calendar_id: str,
    service_date: date,
    job_duration_min: int = DEFAULT_JOB_DURATION_MIN,
    buffer_min: int = DEFAULT_BUFFER_MIN,
) -> List[Dict[str, Any]]:
    """
    Returns a list of available slots for a given day.

    Each slot is a dict with:
      - "start": ISO datetime string
      - "end": ISO datetime string
      - "label": human readable range (e.g. "10:00 AM – 12:00 PM")
      - "is_same_day": bool
      - "is_after_hours": bool (for surcharge logic later)
    """
    # Don't offer slots on blackout dates
    if service_date in BLACKOUT_DATES:
        return []

    weekday = service_date.weekday()
    hours = BUSINESS_HOURS.get(weekday)
    if not hours:
        # Business closed this weekday
        return []

    open_time, close_time = hours

    tz = pytz.timezone(TIMEZONE)

    # Build the day’s working window in local time
    day_start = tz.localize(datetime.combine(service_date, open_time))
    day_end = tz.localize(datetime.combine(service_date, close_time))

    # Fetch busy intervals for that day
    busy_intervals = get_busy_intervals(calendar_id, day_start, day_end)

    job_delta = timedelta(minutes=job_duration_min)
    buffer_delta = timedelta(minutes=buffer_min)

    slots: List[Dict[str, Any]] = []
    cursor = day_start

    now_local = datetime.now(tz)
    is_same_day_today = (service_date == now_local.date())

    while cursor + job_delta <= day_end:
        start = cursor
        end = cursor + job_delta

        # Do not allow times entirely in the past (for same-day logic)
        if end <= now_local:
            cursor += buffer_delta
            continue

        # Check overlap with existing busy intervals (with buffer)
        conflict = False
        for b_start, b_end in busy_intervals:
            # Normalize to local timezone
            b_start = b_start.astimezone(tz)
            b_end = b_end.astimezone(tz)

            # Expand existing bookings with buffer on both ends
            b_start_buffered = b_start - buffer_delta
            b_end_buffered = b_end + buffer_delta

            if _overlaps(start, end, b_start_buffered, b_end_buffered):
                conflict = True
                break

        if not conflict:
            # After-hours flag: you can tweak the threshold time later.
            is_after_hours = start.time() >= dtime(18, 0)  # after 6 PM

            slot = {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "label": start.strftime("%-I:%M %p") + " – " + end.strftime("%-I:%M %p"),
                "is_same_day": is_same_day_today,
                "is_after_hours": is_after_hours,
            }
            slots.append(slot)

        cursor += buffer_delta

    return slots
