# D Assistant — Bulletproof Telegram Bot

A personal AI assistant on Telegram that **never times out, never crashes**.
Powered by **Google Gemini 2.0 Flash** (100% free tier).

## Features
- 🤖 **Gemini AI** — Smart conversational AI with tool use (FREE)
- 🔍 **Real-time web search** — DuckDuckGo (free, no API key)
- 📰 **News search** — Latest headlines on any topic
- 🧠 **Persistent memory** — Remembers things across conversations
- ⏰ **Reminders** — Relative (30m, 2h) or absolute (2025-06-15 09:00)
- 💬 **Conversation history** — Maintains context across messages
- 🛡️ **Never times out** — Typing indicator + timeout fallbacks

## Setup

### 1. Get API Keys (both free)
- **Telegram Bot Token**: [@BotFather](https://t.me/BotFather) on Telegram
- **Google AI API Key**: [aistudio.google.com](https://aistudio.google.com/apikey) (free, no credit card)

### 2. Deploy on Railway
1. Fork this repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variables: `TELEGRAM_BOT_TOKEN`, `GOOGLE_AI_API_KEY`
4. Deploy — bot runs 24/7

### 3. Or run locally
```bash
cp .env.example .env
# Edit .env with your keys
pip install -r requirements.txt
python bot.py
```

## Architecture
```
bot.py          — Main entry, handlers, polling loop
ai_engine.py    — Gemini integration with timeout protection
web_search.py   — DuckDuckGo search (free)
database.py     — SQLite async (memory, history, reminders)
config.py       — Environment variable management
```
