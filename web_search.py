"""
D Assistant Bot — Real-time Web Search (DuckDuckGo, free, no API key)
"""
import asyncio
import httpx
from duckduckgo_search import DDGS


async def search_web(query: str, max_results: int = 5) -> str:
    """Search the web and return formatted results. Never throws."""
    try:
        # Run sync DDG in thread to avoid blocking
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: list(DDGS().text(query, max_results=max_results))
        )
        if not results:
            return f"No results found for: {query}"

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(f"{i}. **{r.get('title', 'No title')}**\n   {r.get('body', 'No description')}\n   🔗 {r.get('href', '')}")
        return "\n\n".join(formatted)

    except Exception as e:
        return f"Search temporarily unavailable: {str(e)}"


async def search_news(query: str, max_results: int = 5) -> str:
    """Search recent news."""
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: list(DDGS().news(query, max_results=max_results))
        )
        if not results:
            return f"No news found for: {query}"

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(f"{i}. **{r.get('title', '')}**\n   {r.get('body', '')}\n   📰 {r.get('source', '')} — {r.get('date', '')}")
        return "\n\n".join(formatted)

    except Exception as e:
        return f"News search temporarily unavailable: {str(e)}"


async def fetch_url(url: str, timeout: int = 15) -> str:
    """Fetch a URL and return its text content (truncated)."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text[:5000]  # Truncate to avoid token bloat
            return text
    except Exception as e:
        return f"Could not fetch URL: {str(e)}"
