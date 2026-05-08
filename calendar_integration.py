"""
D Assistant Bot — Google Calendar Integration
Creates calendar events when users set reminders.
OAuth2 flow happens once via Telegram chat (user clicks link → pastes code).

Requires:
  - GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars
  - Google Cloud project with Calendar API enabled
"""
import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

logger = logging.getLogger("DAssistant.Calendar")

# OAuth scopes
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"  # Manual copy-paste flow

# Token storage (persisted as JSON files per user)
TOKEN_DIR = os.getenv("TOKEN_DIR", "tokens")
Path(TOKEN_DIR).mkdir(exist_ok=True)


def _get_client_config():
    """Build OAuth2 client config from env vars."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


def _token_path(user_id: int) -> str:
    return os.path.join(TOKEN_DIR, f"cal_token_{user_id}.json")


def _save_credentials(user_id: int, creds: Credentials):
    """Persist OAuth tokens for a user."""
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    with open(_token_path(user_id), "w") as f:
        json.dump(data, f)
    logger.info(f"Saved calendar credentials for user {user_id}")


def _load_credentials(user_id: int) -> Credentials | None:
    """Load saved OAuth tokens for a user."""
    path = _token_path(user_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes", SCOPES),
        )
        return creds
    except Exception as e:
        logger.error(f"Failed to load credentials for user {user_id}: {e}")
        return None


def is_calendar_configured() -> bool:
    """Check if Google Calendar OAuth is configured."""
    return _get_client_config() is not None


def is_user_connected(user_id: int) -> bool:
    """Check if a user has authorized Google Calendar."""
    creds = _load_credentials(user_id)
    return creds is not None and creds.refresh_token is not None


def get_auth_url(user_id: int) -> str | None:
    """Generate the Google OAuth authorization URL."""
    config = _get_client_config()
    if not config:
        return None

    flow = Flow.from_client_config(config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return auth_url


def complete_auth(user_id: int, auth_code: str) -> bool:
    """Exchange the authorization code for tokens."""
    config = _get_client_config()
    if not config:
        return False

    try:
        flow = Flow.from_client_config(config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        flow.fetch_token(code=auth_code)
        creds = flow.credentials
        _save_credentials(user_id, creds)
        logger.info(f"Calendar authorization complete for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Calendar auth failed for user {user_id}: {e}")
        return False


def _get_calendar_service(user_id: int):
    """Get an authenticated Google Calendar API service."""
    creds = _load_credentials(user_id)
    if not creds:
        return None

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        try:
            creds.refresh(Request())
            _save_credentials(user_id, creds)
        except Exception as e:
            logger.error(f"Token refresh failed for user {user_id}: {e}")
            return None

    return build("calendar", "v3", credentials=creds)


async def create_calendar_event(
    user_id: int,
    title: str,
    event_time: datetime,
    description: str = "",
    reminder_minutes: int = 10,
) -> dict | None:
    """
    Create a Google Calendar event with a reminder.
    Returns the event dict on success, None on failure.
    """
    loop = asyncio.get_event_loop()

    def _create():
        service = _get_calendar_service(user_id)
        if not service:
            return None

        event = {
            "summary": f"🤖 {title}",
            "description": f"Set via D Assistant Bot\n\n{description}",
            "start": {
                "dateTime": event_time.isoformat(),
                "timeZone": "America/New_York",
            },
            "end": {
                "dateTime": (event_time + timedelta(minutes=15)).isoformat(),
                "timeZone": "America/New_York",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": reminder_minutes},
                    {"method": "popup", "minutes": 0},  # At event time
                ],
            },
        }

        try:
            result = service.events().insert(calendarId="primary", body=event).execute()
            logger.info(f"Calendar event created: {result.get('id')} for user {user_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to create calendar event: {e}")
            return None

    return await loop.run_in_executor(None, _create)


async def list_upcoming_events(user_id: int, max_results: int = 5) -> list:
    """List upcoming calendar events."""
    loop = asyncio.get_event_loop()

    def _list():
        service = _get_calendar_service(user_id)
        if not service:
            return []

        now = datetime.utcnow().isoformat() + "Z"
        try:
            result = service.events().list(
                calendarId="primary",
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            return result.get("items", [])
        except Exception as e:
            logger.error(f"Failed to list events: {e}")
            return []

    return await loop.run_in_executor(None, _list)
