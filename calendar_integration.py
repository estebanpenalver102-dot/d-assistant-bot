"""
D Assistant Bot — Google Calendar Integration
Desktop OAuth2 flow: user clicks sign-in link → authorizes → copies redirect URL → bot extracts code.

Requires:
  - GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET env vars
  - Google Cloud project with Calendar API enabled + Desktop-type OAuth client
"""
import os
import json
import logging
import asyncio
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build

logger = logging.getLogger("DAssistant.Calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TOKEN_DIR = os.getenv("TOKEN_DIR", "/data/tokens")
Path(TOKEN_DIR).mkdir(parents=True, exist_ok=True)

# Localhost redirect for Desktop-type OAuth clients (auto-allowed by Google)
REDIRECT_URI = "http://localhost:9876"

# Track users awaiting URL paste
_awaiting_url: dict[int, str] = {}  # user_id → state token

# Reference to Telegram bot app
_bot_app = None


def _get_client_id():
    return os.getenv("GOOGLE_CLIENT_ID")


def _get_client_secret():
    return os.getenv("GOOGLE_CLIENT_SECRET")


def _token_path(user_id: int) -> str:
    return os.path.join(TOKEN_DIR, f"cal_token_{user_id}.json")


def _save_credentials(user_id: int, creds: Credentials):
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
    path = _token_path(user_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes", SCOPES),
        )
    except Exception as e:
        logger.error(f"Failed to load credentials for user {user_id}: {e}")
        return None


def is_calendar_configured() -> bool:
    return bool(_get_client_id() and _get_client_secret())


def is_user_connected(user_id: int) -> bool:
    creds = _load_credentials(user_id)
    return creds is not None and creds.refresh_token is not None


def get_auth_url(user_id: int) -> str | None:
    """Generate Google OAuth2 authorization URL with localhost redirect."""
    client_id = _get_client_id()
    if not client_id:
        return None

    state = secrets.token_urlsafe(32)
    _awaiting_url[user_id] = state

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def is_awaiting_url(user_id: int) -> bool:
    return user_id in _awaiting_url


async def handle_pasted_url(user_id: int, text: str) -> tuple[bool, str]:
    """
    Process a pasted URL or code from the user.
    Returns (success, message).
    """
    if user_id not in _awaiting_url:
        return False, ""

    text = text.strip()

    # Try to extract the code from a pasted URL or raw code
    code = None

    # Check if it's a full URL (http://localhost:9876?code=...&...)
    if text.startswith("http"):
        try:
            parsed = urlparse(text)
            params = parse_qs(parsed.query)
            if "code" in params:
                code = params["code"][0]
                # Verify state if present
                if "state" in params:
                    expected_state = _awaiting_url.get(user_id)
                    if params["state"][0] != expected_state:
                        _awaiting_url.pop(user_id, None)
                        return True, "❌ State mismatch — security check failed. Try /connect_calendar again."
        except Exception:
            pass

    # If not a URL, try as a raw auth code (starts with 4/ usually)
    if not code and ("/" in text or len(text) > 20):
        code = text

    if not code:
        return True, (
            "🤔 I couldn't find the authorization code in that.\n\n"
            "After signing in, copy the **entire URL** from your browser's address bar "
            "(it starts with `http://localhost:9876...`) and paste it here."
        )

    # Exchange the code for tokens
    _awaiting_url.pop(user_id, None)
    creds = await _exchange_code(code)

    if not creds or not creds.refresh_token:
        return True, "❌ Authorization failed. The code might have expired.\nTry /connect_calendar again to get a fresh link."

    _save_credentials(user_id, creds)
    logger.info(f"Calendar connected for user {user_id}")

    return True, (
        "✅ *Google Calendar connected!*\n\n"
        "From now on, every reminder you set with /remind will also create a "
        "Google Calendar event with a popup notification. 🔔\n\n"
        "Try: `/remind 30m | Test calendar sync`\n"
        "See events: `/calendar`"
    )


async def _exchange_code(auth_code: str) -> Credentials | None:
    """Exchange authorization code for tokens."""
    import aiohttp

    client_id = _get_client_id()
    client_secret = _get_client_secret()

    data = {
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://oauth2.googleapis.com/token", data=data) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Token exchange failed: {resp.status} - {error_text}")
                return None
            token_data = await resp.json()

    return Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def get_fallback_status() -> str:
    """Return AI fallback status for /status command."""
    from ai_engine import _using_fallback
    if _using_fallback:
        return "🧠 AI: Fallback mode (web search)"
    return "🧠 AI: Gemini (active)"


# ============ CALENDAR API OPERATIONS ============

def _get_calendar_service(user_id: int):
    creds = _load_credentials(user_id)
    if not creds:
        return None

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
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
                    {"method": "popup", "minutes": 0},
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
