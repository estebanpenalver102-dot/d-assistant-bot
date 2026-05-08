"""
D Assistant Bot — AI Engine (Google Gemini — FREE tier)
Handles all LLM calls with bulletproof timeout protection.

Free tier: 15 RPM / 1,500 requests per day / 1M tokens per day
Model: Gemini 2.0 Flash — fast, smart, free.
"""
import asyncio
import google.generativeai as genai
import time
from config import GOOGLE_AI_API_KEY, GEMINI_MODEL, MAX_AI_TIMEOUT
from database import get_memories, get_conversation_history, save_memory
from web_search import search_web, search_news

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
- To search news: [NEWS: query]

When the user asks you to remember something, ALWAYS use [REMEMBER: key | value].
When the user asks a factual/current question, use [SEARCH: query] to get real-time info.
When the user wants a reminder, use [REMIND: datetime | text].

You can chain multiple tools in one response. After tool results come back, synthesize a final answer.

Current user memories will be provided in context. Use them to personalize responses."""


def _build_gemini_model():
    """Create a Gemini model instance with system prompt."""
    return genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=2048,
            temperature=0.7,
        ),
    )


async def get_ai_response(user_id: int, user_message: str) -> str:
    """
    Get Gemini's response with full context.
    NEVER times out — returns a fallback if Gemini is slow.
    """
    try:
        # Gather context in parallel
        memories_task = get_memories(user_id, limit=30)
        history_task = get_conversation_history(user_id, limit=15)
        memories, history = await asyncio.gather(memories_task, history_task)

        # Build memory context
        memory_str = ""
        if memories:
            memory_str = "\n\n📝 USER'S SAVED MEMORIES:\n" + "\n".join(
                f"• {m['key']}: {m['value']}" for m in memories
            )

        # Build Gemini chat history
        gemini_history = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        # Create model and chat
        model = _build_gemini_model()
        chat = model.start_chat(history=gemini_history)

        # Prepend memory context to user message
        full_message = user_message
        if memory_str:
            full_message = f"{memory_str}\n\n---\nUser message: {user_message}"

        # Call Gemini with hard timeout (run sync call in thread)
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: chat.send_message(full_message)),
            timeout=MAX_AI_TIMEOUT
        )

        return response.text

    except asyncio.TimeoutError:
        return "⏱ I'm taking longer than usual to think. Let me try a shorter response — could you rephrase or simplify your question?"
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "quota" in error_str.lower():
            return "🔄 I've hit my rate limit. Give me a moment and try again in ~30 seconds."
        elif "API_KEY" in error_str or "api key" in error_str.lower():
            return "⚠️ AI configuration issue — the API key may be missing or invalid. Contact the bot admin."
        return f"⚠️ AI hiccup: {error_str[:200]}. Try again in a moment — I'm still here!"


async def process_tool_calls(user_id: int, ai_response: str) -> tuple:
    """
    Parse AI response for tool commands and execute them.
    Returns (final_response, reminders_list).
    """
    import re

    final_response = ai_response
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

    # Reminders (parsed here, handled in bot.py)
    reminders = re.findall(r'\[REMIND:\s*(.+?)\s*\|\s*(.+?)\]', ai_response)

    if tool_results and not reminders:
        # Re-query Gemini with tool results for a synthesized answer
        try:
            model = _build_gemini_model()
            loop = asyncio.get_event_loop()
            synthesis = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: model.generate_content(
                        f"Synthesize these tool results into a helpful, concise Telegram message. "
                        f"Don't repeat the raw results — summarize and answer naturally.\n\n"
                        f"Original question: {ai_response}\n\nTool results:\n" + "\n\n".join(tool_results)
                    ),
                ),
                timeout=30
            )
            final_response = synthesis.text
        except Exception:
            # Fallback: return raw tool results
            final_response = "\n\n".join(tool_results)

    return final_response, reminders
