import json
import logging
import time

from groq import Groq, RateLimitError

from enrichment.config import GROQ_API_KEY, GROQ_MODEL

log = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)

_SYSTEM_PROMPT = (
    "You are an Operating Systems exam analyst. "
    "Given a JSON array of multiple-choice questions, return a JSON object with a single key 'results' "
    "containing an array — one object per question, in the same order as the input.\n\n"
    "Each object must have exactly these keys:\n"
    '  "id": copy the question id exactly as given,\n'
    '  "explanation": 2-3 sentences — state clearly why the correct answer is right, '
    "then briefly why each wrong option is incorrect (name the option explicitly),\n"
    '  "difficulty_correct": true if the labelled difficulty accurately reflects the question, false otherwise,\n'
    '  "difficulty_suggested": "easy", "medium", or "hard".\n\n'
    "Return ONLY the JSON object. No markdown, no extra text."
)


def _format_question(q: dict) -> dict:
    options = q.get("options", [])
    correct_index = q.get("correct_index")
    try:
        correct_answer = options[int(correct_index)] if correct_index is not None else "N/A"
    except (IndexError, TypeError, ValueError):
        correct_answer = str(correct_index)
    return {
        "id": q.get("id"),
        "topic": q.get("topic", ""),
        "difficulty": q.get("difficulty", "medium"),
        "text": q.get("text", ""),
        "options": options if isinstance(options, list) else [],
        "correct_answer": correct_answer,
    }


def enrich_batch(questions: list[dict], _retry: int = 0) -> dict:
    """
    Send a batch of questions to Groq in a single API call.
    Returns {str(id): result_dict} for all questions in the batch.
    On failure returns {} so the caller can mark them all as failed.
    """
    formatted = [_format_question(q) for q in questions]
    try:
        response = _client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(formatted, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            timeout=60.0,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)
        results = parsed.get("results", [])
        return {str(r["id"]): r for r in results if "id" in r}

    except RateLimitError:
        if _retry < 3:
            wait = 10 * (2 ** _retry)  # 10s, 20s, 40s
            log.warning(f"Rate limit hit — retrying in {wait}s (attempt {_retry + 1}/3)...")
            time.sleep(wait)
            return enrich_batch(questions, _retry + 1)
        log.error(f"Rate limit exceeded after 3 retries for ids={[q.get('id') for q in questions]}")
        return {}

    except Exception as e:
        log.error(f"Groq batch error (ids={[q.get('id') for q in questions]}): {e}")
        return {}