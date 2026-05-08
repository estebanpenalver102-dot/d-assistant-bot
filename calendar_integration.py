"""
D Assistant Bot — Google Calendar Integration via Apps Script Bridge

No OAuth, no API keys, no Cloud Console needed.
User deploys a Google Apps Script as a web app → bot calls it to create/list events.
"""
import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger("DAssistant.Calendar")

TOKEN_DIR = os.getenv("TOKEN_DIR", "/data/tokens")
Path(TOKEN_DIR).mkdir(parents=True, exist_ok=True)


def _bridge_path(user_id: int) -> str:
    return os.path.join(TOKEN_DIR, f"cal_bridge_{user_id}.json")


def _save_bridge_url(user_id: int, url: str):
    with open(_bridge_path(user_id), "w") as f:
        json.dump({"url": url, "connected_at": datetime.utcnow().isoformat()}, f)
    logger.info(f"Saved calendar bridge URL for user {user_id}")


def _load_bridge_url(user_id: int) -> str | None:
    path = _bridge_path(user_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get("url")
    except Exception:
        return None


def is_calendar_configured() -> bool:
    """Always True — no API keys needed for Apps Script bridge."""
    return True


def is_user_connected(user_id: int) -> bool:
    return _load_bridge_url(user_id) is not None


def get_setup_instructions() -> str:
    """Return step-by-step instructions for setting up the Apps Script bridge."""
    return (
        "📅 *Connect Google Calendar* (one-time setup, ~2 min)\n\n"
        "*Step 1:* Click this link to create the calendar bridge:\n"
        "👉 [Open Google Apps Script](https://script.google.com/home/start)\n\n"
        "*Step 2:* Click *\"New project\"* (the blue + button)\n\n"
        "*Step 3:* Delete everything in the editor and paste this code:\n\n"
        "```\n"
        "function doPost(e) {\n"
        "  var d = JSON.parse(e.postData.contents);\n"
        "  var cal = CalendarApp.getDefaultCalendar();\n"
        "  if (d.action == 'create') {\n"
        "    var ev = cal.createEvent(d.title,\n"
        "      new Date(d.start), new Date(d.end));\n"
        "    if (d.reminder) {\n"
        "      ev.removeAllReminders();\n"
        "      ev.addPopupReminder(d.reminder);\n"
        "      ev.addPopupReminder(0);\n"
        "    }\n"
        "    return ContentService.createTextOutput(\n"
        "      JSON.stringify({ok:true, id:ev.getId()}))\n"
        "      .setMimeType(ContentService.MimeType.JSON);\n"
        "  }\n"
        "  if (d.action == 'list') {\n"
        "    var now = new Date();\n"
        "    var end = new Date(now.getTime()+7*86400000);\n"
        "    var evs = cal.getEvents(now, end);\n"
        "    var r = evs.slice(0,10).map(function(e){\n"
        "      return {title:e.getTitle(),\n"
        "        start:e.getStartTime().toISOString(),\n"
        "        end:e.getEndTime().toISOString()};\n"
        "    });\n"
        "    return ContentService.createTextOutput(\n"
        "      JSON.stringify({ok:true, events:r}))\n"
        "      .setMimeType(ContentService.MimeType.JSON);\n"
        "  }\n"
        "}\n"
        "function doGet(e) {\n"
        "  return ContentService.createTextOutput(\n"
        "    JSON.stringify({ok:true}))\n"
        "    .setMimeType(ContentService.MimeType.JSON);\n"
        "}\n"
        "```\n\n"
        "*Step 4:* Click *Deploy* → *New deployment*\n"
        "  • Type: *Web app*\n"
        "  • Execute as: *Me*\n"
        "  • Who has access: *Anyone*\n"
        "  • Click *Deploy*\n\n"
        "*Step 5:* It'll ask for permission — click *\"Review permissions\"* → pick your Google account → *\"Allow\"*\n"
        "_(If you see a warning, click Advanced → Go to Untitled project)_\n\n"
        "*Step 6:* Copy the *Web app URL* and send it to me like this:\n"
        "`/connect_calendar https://script.google.com/macros/s/xxxxx/exec`"
    )


async def verify_bridge(url: str) -> bool:
    """Test that the Apps Script bridge is reachable."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                   allow_redirects=True) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return "ok" in text.lower() or "{" in text
                return False
    except Exception as e:
        logger.error(f"Bridge verification failed: {e}")
        return False


async def connect_user(user_id: int, url: str) -> tuple[bool, str]:
    """Verify and save the bridge URL for a user."""
    url = url.strip()
    if not url.startswith("https://script.google.com/"):
        return False, (
            "❌ That doesn't look like a Google Apps Script URL.\n"
            "It should start with `https://script.google.com/macros/s/...`"
        )

    ok = await verify_bridge(url)
    if not ok:
        return False, (
            "❌ Couldn't reach that script. Make sure:\n"
            "• You clicked *Deploy → New deployment*\n"
            "• Access is set to *Anyone*\n"
            "• You copied the *Web app URL* (not the project URL)\n\n"
            "Try the setup again or paste the corrected URL."
        )

    _save_bridge_url(user_id, url)
    return True, (
        "✅ *Google Calendar connected!*\n\n"
        "Every reminder you set with /remind will now automatically create a "
        "Google Calendar event with popup notifications. 🔔\n\n"
        "Try: `/remind 30m | Test calendar sync`\n"
        "See events: `/calendar`"
    )


def get_calendar_link(title: str, event_time: datetime, duration_min: int = 15) -> str:
    """Generate a Google Calendar one-click add link (fallback for unconnected users)."""
    start = event_time.strftime("%Y%m%dT%H%M%S")
    end = (event_time + timedelta(minutes=duration_min)).strftime("%Y%m%dT%H%M%S")
    params = {
        "action": "TEMPLATE",
        "text": f"🤖 {title}",
        "dates": f"{start}/{end}",
        "details": "Set via D Assistant Bot",
    }
    return f"https://calendar.google.com/calendar/render?{urlencode(params)}"


# ============ CALENDAR API OPERATIONS ============

async def create_calendar_event(
    user_id: int,
    title: str,
    event_time: datetime,
    description: str = "",
    reminder_minutes: int = 10,
) -> dict | None:
    """Create a calendar event via the Apps Script bridge."""
    url = _load_bridge_url(user_id)
    if not url:
        return None

    payload = {
        "action": "create",
        "title": f"🤖 {title}",
        "start": event_time.isoformat(),
        "end": (event_time + timedelta(minutes=15)).isoformat(),
        "reminder": reminder_minutes,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json(content_type=None)
                    if result.get("ok"):
                        logger.info(f"Calendar event created for user {user_id}")
                        return result
                text = await resp.text()
                logger.error(f"Bridge error: {resp.status} - {text}")
    except Exception as e:
        logger.error(f"Failed to create calendar event: {e}")

    return None


async def list_upcoming_events(user_id: int, max_results: int = 5) -> list:
    """List upcoming events via the Apps Script bridge."""
    url = _load_bridge_url(user_id)
    if not url:
        return []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"action": "list"},
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json(content_type=None)
                    if result.get("ok"):
                        return result.get("events", [])[:max_results]
    except Exception as e:
        logger.error(f"Failed to list events: {e}")

    return []
