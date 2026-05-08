"""
D Assistant Bot — Configuration
All settings via environment variables. No hardcoded secrets.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# === REQUIRED ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")      # From @BotFather
GOOGLE_AI_API_KEY = os.getenv("GOOGLE_AI_API_KEY")         # From aistudio.google.com (FREE)

# === OPTIONAL ===
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

# Bot behavior
MAX_AI_TIMEOUT = int(os.getenv("MAX_AI_TIMEOUT", "55"))
TYPING_INTERVAL = int(os.getenv("TYPING_INTERVAL", "4"))
MAX_MEMORY_PER_USER = int(os.getenv("MAX_MEMORY_PER_USER", "500"))
DB_PATH = os.getenv("DB_PATH", "d_assistant.db")

# Gemini models — ordered by preference (fallback chain)
GEMINI_MODELS = [
    os.getenv("GEMINI_MODEL", "gemini-3-flash-preview"),
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]
