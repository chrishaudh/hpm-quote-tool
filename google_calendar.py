from datetime import datetime
from typing import Optional
import os
import json

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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
            "timeZone": "America/New_York",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "America/New_York",
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

