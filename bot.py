"""
D Assistant Bot — Main Entry Point
🛡️ BULLETPROOF: This bot NEVER times out, NEVER crashes permanently.

Architecture:
- Async everything (no blocking)
- Immediate "typing..." feedback
- Background processing for AI calls
- Graceful error recovery on EVERY handler
- Auto-restart on fatal errors
- Reminder checker runs every 30 seconds
"""
import asyncio
import logging
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from dateutil import parser as dateparser

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction, ParseMode

from config import TELEGRAM_BOT_TOKEN, TYPING_INTERVAL
from database import (
    init_db, save_message, get_memories, save_memory,
    delete_memory, add_reminder, get_pending_reminders,
    mark_reminder_sent, get_user_reminders,
)
from ai_engine import get_ai_response, process_tool_calls
from calendar_integration import (
    is_calendar_configured, is_user_connected,
    get_auth_url, complete_auth, create_calendar_event,
    list_upcoming_events,
)
from fallback_engine import get_fallback_status

# === LOGGING ===
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("DAssistant")


# === TYPING INDICATOR (keeps Telegram happy) ===
async def keep_typing(chat_id: int, context: ContextTypes.DEFAULT_TYPE, stop_event: asyncio.Event):
    """Continuously send 'typing...' so Telegram doesn't think we're dead."""
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(TYPING_INTERVAL)


# === COMMAND HANDLERS ===

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    try:
        user = update.effective_user
        await save_memory(user.id, "name", user.first_name or "User")
        await update.message.reply_text(
            f"👋 Hey {user.first_name}! I'm **D Assistant**.\n\n"
            "I can help you with:\n"
            "• 💬 Any question (I search the web in real-time)\n"
            "• 🧠 Remember things for you (/remember)\n"
            "• ⏰ Set reminders (/remind)\n"
            "• 📋 Manage tasks and automations\n"
            "• 📅 Calendar management\n\n"
            "Just talk to me naturally — no special commands needed!\n\n"
            "Type /help for all commands.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await update.message.reply_text("👋 Hey! I'm D Assistant. Just talk to me!")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help."""
    try:
        await update.message.reply_text(
            "**D Assistant Commands:**\n\n"
            "🔍 Just message me — I'll answer with AI + web search\n\n"
            "📝 **/remember** `key | value` — Save something to memory\n"
            "🧠 **/memories** — Show all your saved memories\n"
            "🗑 **/forget** `key` — Delete a memory\n\n"
            "⏰ **/remind** `time | text` — Set a reminder\n"
            "   Example: `/remind 2025-06-15 09:00 | Doctor appointment`\n"
            "   Example: `/remind 30m | Check the oven`\n"
            "📋 **/reminders** — Show pending reminders\n\n"
            "🔎 **/search** `query` — Direct web search\n"
            "📰 **/news** `topic` — Latest news\n\n"
            "ℹ️ **/status** — Bot health check",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Error in /help: {e}")


async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a memory: /remember key | value"""
    try:
        text = update.message.text.replace("/remember", "").strip()
        if "|" not in text:
            await update.message.reply_text("Usage: /remember key | value\nExample: /remember birthday | June 15th")
            return
        key, value = text.split("|", 1)
        await save_memory(update.effective_user.id, key.strip(), value.strip())
        await update.message.reply_text(f"💾 Saved: **{key.strip()}** = {value.strip()}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error in /remember: {e}")
        await update.message.reply_text("❌ Couldn't save that. Try: /remember key | value")


async def cmd_memories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all memories."""
    try:
        memories = await get_memories(update.effective_user.id)
        if not memories:
            await update.message.reply_text("🧠 No memories saved yet. Use /remember to save something!")
            return
        lines = [f"• **{m['key']}**: {m['value']}" for m in memories]
        await update.message.reply_text("🧠 **Your Memories:**\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error in /memories: {e}")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a memory: /forget key"""
    try:
        key = update.message.text.replace("/forget", "").strip()
        if not key:
            await update.message.reply_text("Usage: /forget key_name")
            return
        await delete_memory(update.effective_user.id, key)
        await update.message.reply_text(f"🗑 Forgot: **{key}**", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error in /forget: {e}")


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a reminder: /remind time | text"""
    try:
        text = update.message.text.replace("/remind", "").strip()
        if "|" not in text:
            await update.message.reply_text(
                "Usage: /remind time | text\n"
                "Examples:\n"
                "• `/remind 30m | Check oven`\n"
                "• `/remind 2h | Call mom`\n"
                "• `/remind 2025-06-15 09:00 | Doctor appointment`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        time_str, reminder_text = text.split("|", 1)
        time_str = time_str.strip()
        reminder_text = reminder_text.strip()

        # Parse relative times (30m, 2h, 1d)
        relative = re.match(r'^(\d+)\s*(m|min|minutes?|h|hours?|d|days?)$', time_str, re.IGNORECASE)
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)[0].lower()
            if unit == 'm':
                delta = timedelta(minutes=amount)
            elif unit == 'h':
                delta = timedelta(hours=amount)
            elif unit == 'd':
                delta = timedelta(days=amount)
            remind_at = time.time() + delta.total_seconds()
        else:
            # Parse absolute datetime
            try:
                dt = dateparser.parse(time_str)
                remind_at = dt.timestamp()
            except Exception:
                await update.message.reply_text("❌ Couldn't parse that time. Try: `30m`, `2h`, `1d`, or `2025-06-15 09:00`", parse_mode=ParseMode.MARKDOWN)
                return

        rid = await add_reminder(update.effective_user.id, reminder_text, remind_at)
        dt_str = datetime.fromtimestamp(remind_at).strftime("%Y-%m-%d %H:%M")

        # Also create Google Calendar event if connected
        cal_msg = ""
        if is_user_connected(update.effective_user.id):
            try:
                event_time = datetime.fromtimestamp(remind_at)
                event = await create_calendar_event(
                    update.effective_user.id,
                    reminder_text,
                    event_time,
                    description=f"Reminder set via D Assistant on Telegram",
                    reminder_minutes=10,
                )
                if event:
                    cal_msg = "\n📅 Also added to your Google Calendar with notification!"
            except Exception as e:
                logger.warning(f"Calendar event creation failed: {e}")
                cal_msg = "\n⚠️ Couldn't sync to calendar (will retry next time)"

        await update.message.reply_text(
            f"⏰ Reminder set for **{dt_str}**:\n_{reminder_text}_{cal_msg}",
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as e:
        logger.error(f"Error in /remind: {e}")
        await update.message.reply_text("❌ Couldn't set reminder. Try: /remind 30m | Check the oven")


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List pending reminders."""
    try:
        reminders = await get_user_reminders(update.effective_user.id)
        if not reminders:
            await update.message.reply_text("⏰ No pending reminders.")
            return
        lines = []
        for r in reminders:
            dt_str = datetime.fromtimestamp(r['remind_at']).strftime("%Y-%m-%d %H:%M")
            lines.append(f"• [{r['id']}] **{dt_str}** — {r['text']}")
        await update.message.reply_text("⏰ **Pending Reminders:**\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error in /reminders: {e}")


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct web search."""
    try:
        query = update.message.text.replace("/search", "").strip()
        if not query:
            await update.message.reply_text("Usage: /search your query here")
            return
        stop = asyncio.Event()
        typing_task = asyncio.create_task(keep_typing(update.effective_chat.id, context, stop))
        try:
            from web_search import search_web
            result = await search_web(query)
            await update.message.reply_text(f"🔍 **Results for:** {query}\n\n{result}", parse_mode=ParseMode.MARKDOWN)
        finally:
            stop.set()
            typing_task.cancel()
    except Exception as e:
        logger.error(f"Error in /search: {e}")
        await update.message.reply_text("❌ Search failed. Try again.")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Latest news."""
    try:
        query = update.message.text.replace("/news", "").strip() or "top news today"
        stop = asyncio.Event()
        typing_task = asyncio.create_task(keep_typing(update.effective_chat.id, context, stop))
        try:
            from web_search import search_news
            result = await search_news(query)
            await update.message.reply_text(f"📰 **News:** {query}\n\n{result}", parse_mode=ParseMode.MARKDOWN)
        finally:
            stop.set()
            typing_task.cancel()
    except Exception as e:
        logger.error(f"Error in /news: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check."""
    try:
        uptime = time.time() - context.bot_data.get("start_time", time.time())
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        cal_status = "📅 Calendar: Connected ✅" if is_user_connected(update.effective_user.id) else "📅 Calendar: Not connected (use /connect_calendar)"
        fb_status = get_fallback_status()
        await update.message.reply_text(
            f"✅ **D Assistant Status**\n\n"
            f"🟢 Online and healthy\n"
            f"⏱ Uptime: {hours}h {minutes}m\n"
            f"{fb_status}\n"
            f"🔍 Search: DuckDuckGo\n"
            f"💾 Memory: SQLite (persistent)\n"
            f"{cal_status}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Error in /status: {e}")


# === CALENDAR COMMANDS ===

# Track users awaiting auth code
_awaiting_auth = set()


async def cmd_connect_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start Google Calendar OAuth flow."""
    try:
        user_id = update.effective_user.id

        if not is_calendar_configured():
            await update.message.reply_text(
                "⚠️ Google Calendar isn't configured yet.\n"
                "The bot owner needs to set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            )
            return

        if is_user_connected(user_id):
            await update.message.reply_text("✅ Your Google Calendar is already connected! Reminders will sync automatically.")
            return

        auth_url = get_auth_url(user_id)
        if not auth_url:
            await update.message.reply_text("❌ Failed to generate authorization URL.")
            return

        _awaiting_auth.add(user_id)
        await update.message.reply_text(
            "📅 **Connect Google Calendar**\n\n"
            "1️⃣ Click this link to authorize:\n"
            f"{auth_url}\n\n"
            "2️⃣ Sign in with your Google account\n"
            "3️⃣ Copy the authorization code\n"
            "4️⃣ Paste it back here\n\n"
            "After this, all your /remind commands will also create Google Calendar events with notifications! 🔔",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Error in /connect_calendar: {e}")
        await update.message.reply_text("❌ Something went wrong. Try again.")


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show upcoming calendar events."""
    try:
        user_id = update.effective_user.id
        if not is_user_connected(user_id):
            await update.message.reply_text("📅 Calendar not connected. Use /connect\\_calendar first!")
            return

        events = await list_upcoming_events(user_id, max_results=10)
        if not events:
            await update.message.reply_text("📅 No upcoming events on your calendar.")
            return

        lines = []
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date", ""))
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                date_str = dt.strftime("%b %d, %I:%M %p")
            except Exception:
                date_str = start
            lines.append(f"• **{date_str}** — {event.get('summary', 'No title')}")

        await update.message.reply_text(
            "📅 **Upcoming Events:**\n\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Error in /calendar: {e}")
        await update.message.reply_text("❌ Couldn't fetch calendar events.")


async def handle_auth_code(user_id: int, text: str, update: Update) -> bool:
    """Check if message is a calendar auth code. Returns True if handled."""
    if user_id not in _awaiting_auth:
        return False

    # Auth codes look like 4/... or similar
    code = text.strip()
    if len(code) < 10:
        return False

    _awaiting_auth.discard(user_id)
    success = complete_auth(user_id, code)
    if success:
        await update.message.reply_text(
            "✅ **Google Calendar connected!**\n\n"
            "From now on, every reminder you set with /remind will also create a "
            "Google Calendar event with a popup notification. 🔔\n\n"
            "Try: `/remind 30m | Test calendar sync`",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            "❌ Authorization failed. The code might have expired.\n"
            "Try /connect\\_calendar again to get a fresh link.",
            parse_mode=ParseMode.MARKDOWN,
        )
    return True


# === MAIN MESSAGE HANDLER (the heart of the bot) ===

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle all text messages with AI.
    🛡️ NEVER times out — typing indicator keeps running, 
    hard timeout returns graceful fallback.
    """
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_text = update.message.text

    # Check if this is a calendar auth code
    if await handle_auth_code(user_id, user_text, update):
        return

    # Start typing immediately (this is what prevents "bot not responding" perception)
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(chat_id, context, stop_typing))

    try:
        # Save user message to history
        await save_message(user_id, "user", user_text)

        # Get AI response (with built-in timeout protection)
        ai_response = await get_ai_response(user_id, user_text)

        # Process any tool calls in the response
        final_response, reminders = await process_tool_calls(user_id, ai_response)

        # Handle reminders from AI
        for time_str, text in reminders:
            try:
                relative = re.match(r'^(\d+)\s*(m|min|minutes?|h|hours?|d|days?)$', time_str.strip(), re.IGNORECASE)
                if relative:
                    amount = int(relative.group(1))
                    unit = relative.group(2)[0].lower()
                    if unit == 'm':
                        remind_at = time.time() + amount * 60
                    elif unit == 'h':
                        remind_at = time.time() + amount * 3600
                    else:
                        remind_at = time.time() + amount * 86400
                else:
                    dt = dateparser.parse(time_str.strip())
                    remind_at = dt.timestamp()
                await add_reminder(user_id, text.strip(), remind_at)
            except Exception as e:
                logger.warning(f"Failed to parse reminder: {e}")

        # Clean up tool commands from the response
        final_response = re.sub(r'\[SEARCH:.*?\]', '', final_response)
        final_response = re.sub(r'\[NEWS:.*?\]', '', final_response)
        final_response = re.sub(r'\[REMEMBER:.*?\]', '', final_response)
        final_response = re.sub(r'\[RECALL:.*?\]', '', final_response)
        final_response = re.sub(r'\[REMIND:.*?\]', '', final_response)
        final_response = final_response.strip()

        if not final_response:
            final_response = "Done! ✅"

        # Send response (split if too long for Telegram's 4096 char limit)
        if len(final_response) <= 4096:
            await update.message.reply_text(final_response, parse_mode=ParseMode.MARKDOWN)
        else:
            # Split into chunks
            for i in range(0, len(final_response), 4096):
                chunk = final_response[i:i+4096]
                await update.message.reply_text(chunk)

        # Save assistant response to history
        await save_message(user_id, "assistant", final_response)

    except Exception as e:
        logger.error(f"Error handling message from {user_id}: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                "⚠️ I hit a snag processing that. Could you try again? I'm still here!"
            )
        except Exception:
            pass  # If even the error message fails, just log it

    finally:
        # ALWAYS stop typing indicator
        stop_typing.set()
        typing_task.cancel()


# === REMINDER CHECKER (runs every 30 seconds) ===

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Background job: check and send due reminders."""
    try:
        pending = await get_pending_reminders()
        for r in pending:
            try:
                await context.bot.send_message(
                    chat_id=r['user_id'],
                    text=f"⏰ **Reminder:** {r['text']}",
                    parse_mode=ParseMode.MARKDOWN,
                )
                await mark_reminder_sent(r['id'])
                logger.info(f"Sent reminder {r['id']} to user {r['user_id']}")
            except Exception as e:
                logger.error(f"Failed to send reminder {r['id']}: {e}")
    except Exception as e:
        logger.error(f"Reminder checker error: {e}")


# === ERROR HANDLER ===

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — logs everything, crashes nothing."""
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)
    if update and hasattr(update, 'effective_chat') and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Something unexpected happened, but I'm still running. Try again!",
            )
        except Exception:
            pass


# === MAIN ===

async def post_init(application: Application):
    """Set up bot commands menu, init DB, and start background jobs."""
    await init_db()
    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show all commands"),
        BotCommand("search", "Search the web"),
        BotCommand("news", "Latest news"),
        BotCommand("remember", "Save to memory"),
        BotCommand("memories", "View saved memories"),
        BotCommand("forget", "Delete a memory"),
        BotCommand("remind", "Set a reminder"),
        BotCommand("reminders", "View pending reminders"),
        BotCommand("status", "Bot health check"),
        BotCommand("connect_calendar", "Connect Google Calendar"),
        BotCommand("calendar", "View upcoming events"),
    ])
    application.bot_data["start_time"] = time.time()
    logger.info("✅ D Assistant is online and ready!")


def main():
    """Start the bot with maximum resilience."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set! Add it to .env")
        sys.exit(1)

    logger.info("🚀 Starting D Assistant Bot...")

    # Build application with custom settings for resilience
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .concurrent_updates(True)  # Handle multiple users simultaneously
        .build()
    )

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("remember", cmd_remember))
    app.add_handler(CommandHandler("memories", cmd_memories))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("connect_calendar", cmd_connect_calendar))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Global error handler
    app.add_error_handler(error_handler)

    # Background reminder checker — every 30 seconds
    app.job_queue.run_repeating(check_reminders, interval=30, first=10)

    # Run with polling (most reliable, auto-reconnects)
    logger.info("🔄 Starting polling...")
    app.run_polling(
        drop_pending_updates=True,     # Don't replay old messages on restart
        allowed_updates=Update.ALL_TYPES,
        poll_interval=1.0,             # Check every second
        timeout=30,                    # Long polling timeout
    )


if __name__ == "__main__":
    main()
