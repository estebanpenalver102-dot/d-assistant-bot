"""
D Assistant Bot — Persistent Memory & Task Storage (SQLite async)
Never loses data. Never forgets.
"""
import aiosqlite
import json
import time
from config import DB_PATH, MAX_MEMORY_PER_USER


async def init_db():
    """Create tables on startup."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_user ON memory(user_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_user_key ON memory(user_id, key);

            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                cron_expression TEXT,
                next_run REAL,
                is_active INTEGER DEFAULT 1,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_active ON tasks(is_active, next_run);

            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_at REAL NOT NULL,
                sent INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(sent, remind_at);
        """)
        await db.commit()


# === MEMORY ===

async def save_memory(user_id: int, key: str, value: str):
    """Save or update a memory entry for a user."""
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO memory (user_id, key, value, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, key) DO UPDATE SET value=?, updated_at=?""",
            (user_id, key, value, now, now, value, now)
        )
        await db.commit()


async def get_memories(user_id: int, limit: int = 50) -> list[dict]:
    """Get all memories for a user (most recent first)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT key, value, updated_at FROM memory WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def delete_memory(user_id: int, key: str):
    """Delete a specific memory."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM memory WHERE user_id=? AND key=?", (user_id, key))
        await db.commit()


async def search_memories(user_id: int, query: str) -> list[dict]:
    """Simple keyword search across memory keys and values."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT key, value FROM memory WHERE user_id=? AND (key LIKE ? OR value LIKE ?) LIMIT 20",
            (user_id, f"%{query}%", f"%{query}%")
        )
        return [dict(r) for r in await cursor.fetchall()]


# === CONVERSATION HISTORY ===

async def save_message(user_id: int, role: str, content: str):
    """Save a conversation message."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, role, content, time.time())
        )
        # Keep only last N messages per user
        await db.execute(
            """DELETE FROM conversations WHERE id NOT IN (
                SELECT id FROM conversations WHERE user_id=? ORDER BY timestamp DESC LIMIT ?
            ) AND user_id=?""",
            (user_id, MAX_MEMORY_PER_USER, user_id)
        )
        await db.commit()


async def get_conversation_history(user_id: int, limit: int = 20) -> list[dict]:
    """Get recent conversation history."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT role, content FROM conversations WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]


# === REMINDERS ===

async def add_reminder(user_id: int, text: str, remind_at: float) -> int:
    """Add a reminder. Returns the reminder ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, text, remind_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, text, remind_at, time.time())
        )
        await db.commit()
        return cursor.lastrowid


async def get_pending_reminders() -> list[dict]:
    """Get all unsent reminders that are due."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, user_id, text FROM reminders WHERE sent=0 AND remind_at <= ?",
            (time.time(),)
        )
        return [dict(r) for r in await cursor.fetchall()]


async def mark_reminder_sent(reminder_id: int):
    """Mark a reminder as sent."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
        await db.commit()


async def get_user_reminders(user_id: int) -> list[dict]:
    """Get all active reminders for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, text, remind_at FROM reminders WHERE user_id=? AND sent=0 ORDER BY remind_at",
            (user_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]
