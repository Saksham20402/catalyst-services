import json
import logging
import time

import httpx

from enrichment.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_MAX_RETRIES

log = logging.getLogger(__name__)

# Shared client — reuses the TCP connection across all LLM calls (eliminates
# per-request handshake overhead; critical when processing hundreds of questions)
_http = httpx.Client(
    base_url=OLLAMA_BASE_URL,
    timeout=OLLAMA_TIMEOUT,
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)

# Lean prompt: no unused fields (relevance_reason, difficulty_correct removed).
# Asking the model to independently assess difficulty — not to "validate" a label —
# avoids anchoring bias and always gives us a fresh AI-rated difficulty to store.
_PROMPT = """\
You are an exam question analyst. Return ONLY valid JSON — no markdown, no extra text.

Subject: {subject}
Topic: {topic}
Question: {text}
Options: {options}
Correct answer: {correct_answer}

Respond with this exact JSON:
{{
  "explanation": "<step-by-step explanation of why the correct answer is right and each wrong option is incorrect — clear enough for a student who picked the wrong answer>",
  "difficulty": "easy|medium|hard"
}}

Difficulty rules:
- easy   = single concept, directly stated in the question
- medium = requires applying or combining two concepts
- hard   = requires multi-step reasoning, subtle distinctions, or deep domain knowledge
"""


def _build_prompt(q: dict, context: str = "") -> str:
    options = q.get("options", [])
    correct_index = q.get("correct_index")
    try:
        correct_answer = options[int(correct_index)] if correct_index is not None else "N/A"
    except (IndexError, TypeError, ValueError):
        correct_answer = str(correct_index)

    prompt = _PROMPT.format(
        subject=q.get("subject", "General"),
        topic=q.get("topic", ""),
        text=q.get("text", ""),
        options=", ".join(f"{i}. {o}" for i, o in enumerate(options)) if isinstance(options, list) else str(options),
        correct_answer=correct_answer,
    )

    if context:
        prompt = f"Background context: {context}\n\n" + prompt

    return prompt


def enrich_with_ollama(q: dict, context: str = "") -> dict:
    """
    Call local Ollama and return the parsed enrichment dict, or {} on failure.
    Retries up to OLLAMA_MAX_RETRIES times with exponential back-off.
    """
    prompt = _build_prompt(q, context)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }

    for attempt in range(1, OLLAMA_MAX_RETRIES + 2):
        try:
            resp = _http.post("/api/generate", json=payload)
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            return json.loads(raw)

        except httpx.HTTPStatusError as e:
            log.error(
                f"id={q.get('id')} Ollama HTTP {e.response.status_code} "
                f"(attempt {attempt}): {e.response.text[:200]}"
            )
            if e.response.status_code == 404:
                log.error(f"  → Model '{OLLAMA_MODEL}' not found. Run: ollama pull {OLLAMA_MODEL}")
                return {}

        except httpx.RequestError as e:
            log.error(f"id={q.get('id')} Ollama connection error (attempt {attempt}): {e}")

        except (json.JSONDecodeError, KeyError) as e:
            log.error(f"id={q.get('id')} Ollama bad JSON (attempt {attempt}): {e}")
            break  # malformed JSON won't improve on retry

        if attempt <= OLLAMA_MAX_RETRIES:
            time.sleep(2 ** attempt)  # 2s, 4s, ...

    return {}