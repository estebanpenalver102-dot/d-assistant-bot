"""
D Assistant Bot — Fallback Engine
When Gemini hits rate limits, this kicks in with intelligent cached responses
using DuckDuckGo search + pattern matching. Automatically switches back
when the 24h quota resets.

Strategy:
1. Track when rate limit was first hit
2. Use web search + templates for answers during cooldown
3. After 24h, try Gemini again → auto-recover
"""
import asyncio
import time
import logging
from datetime import datetime, timedelta
from web_search import search_web, search_news

logger = logging.getLogger("DAssistant.Fallback")

# Global fallback state
_fallback_state = {
    "active": False,
    "activated_at": None,
    "request_count": 0,
    "recovery_check_at": None,
}

COOLDOWN_HOURS = 24
RECOVERY_CHECK_INTERVAL = 300  # Try Gemini every 5 min after cooldown


def is_fallback_active() -> bool:
    """Check if we're in fallback mode."""
    if not _fallback_state["active"]:
        return False

    # Auto-recover after 24 hours
    activated = _fallback_state["activated_at"]
    if activated and (time.time() - activated) > (COOLDOWN_HOURS * 3600):
        logger.info("🔄 24h cooldown passed — attempting Gemini recovery")
        deactivate_fallback()
        return False

    return True


def activate_fallback():
    """Switch to fallback mode."""
    if not _fallback_state["active"]:
        _fallback_state["active"] = True
        _fallback_state["activated_at"] = time.time()
        _fallback_state["request_count"] = 0
        logger.warning("⚠️ Fallback mode ACTIVATED — Gemini rate limited")


def deactivate_fallback():
    """Switch back to Gemini."""
    _fallback_state["active"] = False
    _fallback_state["activated_at"] = None
    _fallback_state["request_count"] = 0
    _fallback_state["recovery_check_at"] = None
    logger.info("✅ Fallback mode DEACTIVATED — back to Gemini")


def should_try_recovery() -> bool:
    """Periodically try Gemini even during fallback to recover early."""
    now = time.time()
    last_check = _fallback_state.get("recovery_check_at") or 0
    if (now - last_check) >= RECOVERY_CHECK_INTERVAL:
        _fallback_state["recovery_check_at"] = now
        return True
    return False


def get_fallback_status() -> str:
    """Human-readable status for /status command."""
    if not _fallback_state["active"]:
        return "🧠 AI: Gemini (primary)"

    activated = _fallback_state["activated_at"]
    elapsed = time.time() - activated if activated else 0
    remaining = max(0, (COOLDOWN_HOURS * 3600) - elapsed)
    hours = int(remaining // 3600)
    minutes = int((remaining % 3600) // 60)
    return (
        f"🔄 AI: Fallback mode (Gemini rate limited)\n"
        f"   Handled {_fallback_state['request_count']} requests in fallback\n"
        f"   Auto-recovery in ~{hours}h {minutes}m"
    )


# === Response templates for common queries ===
GREETING_RESPONSES = [
    "Hey! I'm running on backup power right now (AI quota reset coming soon), but I can still help! What do you need?",
    "Hi there! My main brain is recharging, but I'm still here and capable. What can I do for you?",
    "Hello! Running in lite mode for a bit, but still ready to help. Fire away!",
]

HELP_RESPONSE = """I'm currently in lite mode (AI quota recharging), but I can still:

🔍 **Search the web** — /search [query]
📰 **Get latest news** — /news [topic]
🧠 **Remember things** — /remember [key] | [value]
📋 **Show memories** — /memories
⏰ **Set reminders** — /remind [time] | [text]

My full AI brain comes back online automatically. Most questions I can still answer using web search!"""


async def get_fallback_response(user_message: str) -> str:
    """
    Generate an intelligent response without Gemini.
    Uses web search + pattern matching for quality answers.
    """
    _fallback_state["request_count"] = _fallback_state.get("request_count", 0) + 1
    msg = user_message.lower().strip()

    # Greetings
    if msg in ("hi", "hello", "hey", "yo", "sup", "start", "/start"):
        import random
        return random.choice(GREETING_RESPONSES)

    # Help
    if msg in ("help", "/help", "what can you do", "what can you do?"):
        return HELP_RESPONSE

    # Status
    if msg in ("status", "/status"):
        return get_fallback_status()

    # For everything else, try web search
    try:
        search_result = await search_web(user_message)
        if search_result and "No results" not in search_result:
            return (
                f"🔍 Here's what I found (running in lite mode right now):\n\n"
                f"{search_result}\n\n"
                f"_My full AI will be back soon for deeper analysis!_"
            )
    except Exception as e:
        logger.warning(f"Fallback search failed: {e}")

    # Try news if it looks like a news query
    news_keywords = ["news", "latest", "today", "happening", "update", "current"]
    if any(kw in msg for kw in news_keywords):
        try:
            news_result = await search_news(user_message)
            if news_result and "No results" not in news_result:
                return f"📰 Latest I found:\n\n{news_result}"
        except Exception:
            pass

    # Ultimate fallback
    return (
        "🔄 I'm in lite mode right now (AI quota recharging). "
        "I searched the web but couldn't find a clear answer for that specific question.\n\n"
        "Try:\n"
        "• `/search [your question]` — for web results\n"
        "• `/news [topic]` — for latest news\n"
        "• Or ask me again shortly — my full AI comes back automatically!"
    )
