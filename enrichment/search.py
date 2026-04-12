import logging
from ddgs import DDGS

log = logging.getLogger(__name__)


def search_context(query: str, max_results: int = 3) -> str:
    """Return a brief text snippet from DuckDuckGo for the given query."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        snippets = [r.get("body", "") for r in results if r.get("body")]
        return " ".join(snippets).strip()
    except Exception as e:
        log.warning(f"Search failed for '{query[:60]}': {e}")
        return ""