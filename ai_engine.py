"""
D Assistant Bot — AI Engine (Google Gemini — FREE tier)
Handles all LLM calls with bulletproof timeout protection + model fallback chain.

Uses the newest available Gemini model; falls back automatically if one is overloaded.
"""
import asyncio
import logging
import google.generativeai as genai
from config import GOOGLE_AI_API_KEY, GEMINI_MODELS, MAX_AI_TIMEOUT
from database import get_memories, get_conversation_history, save_memory
from web_search import search_web, search_news

logger = logging.getLogger("DAssistant.AI")

# Configure Gemini
genai.configure(api_key=GOOGLE_AI_API_KEY)

SYSTEM_PROMPT = """You are D Assistant — a smart, efficient personal AI assistant on Telegram.

Your capabilities:
- Answer any question with real-time web search when needed
- Set reminders and manage calendars
- Remember things the user tells you (persistent memory)
- Run automated tasks
- Be conversational but concise — this is Telegram, not an essay

Personality: Helpful, direct, occasionally witty. Like a competent friend who gets things done.

TOOLS AVAILABLE (use by responding with structured commands):
- To search the web: [SEARCH: your query here]
- To save a memory: [REMEMBER: key | value]
- To recall memories: [RECALL: search term]
- To set a reminder: [REMIND: YYYY-MM-DD HH:MM | reminder text]
  OR for relative: [REMIND: 30m | reminder text] or [REMIND: 2h | text]
- To search news: [NEWS: query]

IMPORTANT RULES:
- When the user asks you to remember something, ALWAYS use [REMEMBER: key | value].
- When the user asks a factual or current question, use [SEARCH: query] to get real-time info.
- When the user wants a reminder, use [REMIND: datetime | text].
- You can chain multiple tools in one response.
- After tool results come back, you will synthesize a final answer.
- Keep responses concise — this is a chat, not an article.
- Use markdown formatting sparingly (bold for emphasis only).

Current user memories will be provided in context. Use them to personalize responses."""


def _build_model(model_name: str):
    """Create a Gemini model instance."""
    return genai.GenerativeModel(
        model_name=model_name,
        system_instruction=SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=2048,
            temperature=0.7,
        ),
    )


async def _try_generate(model_name: str, history: list, message: str, timeout: int) -> str | None:
    """Try one model. Returns response text or None on failure."""
    try:
        model = _build_model(model_name)
        gemini_history = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        chat = model.start_chat(history=gemini_history)
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: chat.send_message(message)),
            timeout=timeout,
        )
        logger.info(f"✅ Response from {model_name}")
        return response.text
    except Exception as e:
        logger.warning(f"⚠️ {model_name} failed: {e}")
        return None


async def get_ai_response(user_id: int, user_message: str) -> str:
    """
    Get Gemini response with full context + model fallback chain.
    Tries each model in GEMINI_MODELS until one works.
    NEVER times out — always returns something useful.
    """
    try:
        # Gather context in parallel
        memories, history = await asyncio.gather(
            get_memories(user_id, limit=30),
            get_conversation_history(user_id, limit=15),
        )

        # Build the full message with memory context
        memory_str = ""
        if memories:
            memory_str = "\n\n📝 USER'S SAVED MEMORIES:\n" + "\n".join(
                f"• {m['key']}: {m['value']}" for m in memories
            )
        full_message = f"{memory_str}\n\n---\nUser message: {user_message}" if memory_str else user_message

        # Try each model in the fallback chain
        per_model_timeout = MAX_AI_TIMEOUT // max(len(GEMINI_MODELS), 1)
        for model_name in GEMINI_MODELS:
            result = await _try_generate(model_name, history, full_message, per_model_timeout)
            if result:
                return result

        return "⏱ All AI models are busy right now. Give me a moment and try again — I'll be back!"

    except Exception as e:
        logger.error(f"AI engine error: {e}", exc_info=True)
        return f"⚠️ Something went wrong: {str(e)[:200]}. Try again in a moment!"


async def process_tool_calls(user_id: int, ai_response: str) -> tuple:
    """
    Parse AI response for tool commands and execute them.
    Returns (final_response, reminders_list).
    """
    import re

    tool_results = []

    # Search
    searches = re.findall(r'\[SEARCH:\s*(.+?)\]', ai_response)
    for query in searches:
        result = await search_web(query.strip())
        tool_results.append(f"🔍 Search results for '{query.strip()}':\n{result}")

    # News
    news_queries = re.findall(r'\[NEWS:\s*(.+?)\]', ai_response)
    for query in news_queries:
        result = await search_news(query.strip())
        tool_results.append(f"📰 News for '{query.strip()}':\n{result}")

    # Remember
    remembers = re.findall(r'\[REMEMBER:\s*(.+?)\s*\|\s*(.+?)\]', ai_response)
    for key, value in remembers:
        await save_memory(user_id, key.strip(), value.strip())
        tool_results.append(f"💾 Saved: {key.strip()} = {value.strip()}")

    # Recall
    recalls = re.findall(r'\[RECALL:\s*(.+?)\]', ai_response)
    for query in recalls:
        from database import search_memories
        results = await search_memories(user_id, query.strip())
        if results:
            mem_str = "\n".join(f"• {r['key']}: {r['value']}" for r in results)
            tool_results.append(f"🧠 Memories matching '{query.strip()}':\n{mem_str}")
        else:
            tool_results.append(f"🧠 No memories found for '{query.strip()}'")

    # Reminders (parsed here, executed in bot.py)
    reminders = re.findall(r'\[REMIND:\s*(.+?)\s*\|\s*(.+?)\]', ai_response)

    if tool_results and not reminders:
        # Re-query Gemini with tool results for synthesis
        try:
            model = _build_model(GEMINI_MODELS[0])
            loop = asyncio.get_event_loop()
            synthesis = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: model.generate_content(
                        f"Synthesize these tool results into a helpful, concise Telegram message. "
                        f"Don't repeat raw results — summarize and answer naturally.\n\n"
                        f"Original question context: {ai_response}\n\nTool results:\n" + "\n\n".join(tool_results)
                    ),
                ),
                timeout=30,
            )
            return synthesis.text, reminders
        except Exception:
            return "\n\n".join(tool_results), reminders

    return ai_response, reminders
