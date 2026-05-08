"""
D Assistant Bot — Google Calendar Integration
Web-based OAuth2 flow: user clicks link → signs into any Google account → 
auto-redirected back → bot stores tokens → done.

Requires:
  - GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, RAILWAY_PUBLIC_URL env vars
  - Google Cloud project with Calendar API enabled
  - OAuth redirect URI set to: https://<RAILWAY_PUBLIC_URL>/oauth/callback
"""
import os
import json
import logging
import asyncio
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build

logger = logging.getLogger("DAssistant.Calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TOKEN_DIR = os.getenv("TOKEN_DIR", "/data/tokens")
Path(TOKEN_DIR).mkdir(parents=True, exist_ok=True)

# In-memory state map: state_token → telegram_user_id
_pending_auth: dict[str, int] = {}

# Reference to the Telegram bot app (set during init)
_bot_app = None


def _get_redirect_uri():
    """Build the OAuth2 redirect URI from the Railway public URL."""
    base = os.getenv("RAILWAY_PUBLIC_URL", "").rstrip("/")
    if not base:
        return None
    if not base.startswith("http"):
        base = f"https://{base}"
    return f"{base}/oauth/callback"


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
    return bool(_get_client_id() and _get_client_secret() and _get_redirect_uri())


def is_user_connected(user_id: int) -> bool:
    creds = _load_credentials(user_id)
    return creds is not None and creds.refresh_token is not None


def get_auth_url(user_id: int) -> str | None:
    """Generate Google OAuth2 authorization URL with web redirect."""
    client_id = _get_client_id()
    redirect_uri = _get_redirect_uri()
    if not client_id or not redirect_uri:
        return None

    # Generate a unique state token to map callback → user
    state = secrets.token_urlsafe(32)
    _pending_auth[state] = user_id

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def _exchange_code(auth_code: str) -> Credentials | None:
    """Exchange authorization code for tokens."""
    import aiohttp

    client_id = _get_client_id()
    client_secret = _get_client_secret()
    redirect_uri = _get_redirect_uri()

    data = {
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
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


# ============ AIOHTTP WEB HANDLERS ============

async def handle_oauth_callback(request: web.Request) -> web.Response:
    """Handle Google OAuth2 callback redirect."""
    code = request.query.get("code")
    state = request.query.get("state")
    error = request.query.get("error")

    if error:
        logger.warning(f"OAuth error: {error}")
        return web.Response(
            text=_html_page("Authorization Cancelled",
                           "You cancelled the sign-in. Go back to Telegram and try /connect_calendar again."),
            content_type="text/html",
        )

    if not code or not state:
        return web.Response(
            text=_html_page("Invalid Request", "Missing authorization code or state."),
            content_type="text/html",
            status=400,
        )

    user_id = _pending_auth.pop(state, None)
    if user_id is None:
        return web.Response(
            text=_html_page("Link Expired",
                           "This authorization link has expired. Go back to Telegram and use /connect_calendar to get a fresh link."),
            content_type="text/html",
            status=400,
        )

    # Exchange code for tokens
    creds = await _exchange_code(code)
    if not creds or not creds.refresh_token:
        return web.Response(
            text=_html_page("Authorization Failed",
                           "Couldn't complete the sign-in. Please try /connect_calendar again in Telegram."),
            content_type="text/html",
            status=500,
        )

    # Save credentials
    _save_credentials(user_id, creds)
    logger.info(f"Calendar connected for user {user_id}")

    # Send confirmation message to the user via Telegram
    try:
        if _bot_app and _bot_app.bot:
            await _bot_app.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ *Google Calendar connected!*\n\n"
                    "From now on, every reminder you set with /remind will also create a "
                    "Google Calendar event with a popup notification. 🔔\n\n"
                    "Try: `/remind 30m | Test calendar sync`\n"
                    "See events: `/calendar`"
                ),
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.warning(f"Failed to send Telegram confirmation: {e}")

    return web.Response(
        text=_html_page("Connected! ✅",
                       "Google Calendar is now linked to D Assistant.<br><br>"
                       "You can close this tab and go back to Telegram. 🤖📅"),
        content_type="text/html",
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


def _html_page(title: str, message: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — D Assistant</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       display: flex; align-items: center; justify-content: center; min-height: 100vh;
       margin: 0; background: #0f0f0f; color: #fff; }}
.card {{ background: #1a1a2e; border-radius: 16px; padding: 40px; max-width: 420px;
         text-align: center; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }}
h1 {{ margin: 0 0 16px; font-size: 24px; }}
p {{ color: #aaa; line-height: 1.6; }}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p></div></body></html>"""


# ============ WEB SERVER LIFECYCLE ============

_web_runner = None

async def start_web_server(bot_app=None, port: int = None):
    """Start the aiohttp web server for OAuth callbacks."""
    global _bot_app, _web_runner
    _bot_app = bot_app

    if port is None:
        port = int(os.getenv("PORT", "8080"))

    app = web.Application()
    app.router.add_get("/oauth/callback", handle_oauth_callback)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)

    _web_runner = web.AppRunner(app)
    await _web_runner.setup()
    site = web.TCPSite(_web_runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Web server started on port {port}")


async def stop_web_server():
    global _web_runner
    if _web_runner:
        await _web_runner.cleanup()
        _web_runner = None


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
