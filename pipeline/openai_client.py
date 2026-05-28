import json
import logging
import time

from openai import OpenAI, RateLimitError

from enrichment.config import OPENAI_API_KEY, OPENAI_MODEL

log = logging.getLogger(__name__)

_client = OpenAI(api_key=OPENAI_API_KEY)

_SYSTEM_PROMPT = (
    "You are an exam question analyst and code execution verifier. "
    "Given a JSON array of multiple-choice questions that each contain a code snippet, "
    "return a JSON object with a single key 'results' containing an array — "
    "one object per question, in the same order as input.\n\n"
    "For each question you MUST:\n"
    "1. Mentally trace or execute the code snippet to determine the correct output.\n"
    "2. Compare your result against the provided correct_answer.\n\n"
    "Each result object must have exactly these keys:\n"
    '  "id": copy the question id exactly as given,\n'
    '  "explanation": 2-3 sentences — walk through the code execution, '
    "state why the correct answer is right, briefly why each wrong option is wrong,\n"
    '  "answer_verified": true if correct_answer matches your traced result, false otherwise,\n'
    '  "corrected_correct_index": the 0-based index of the actually correct option '
    'if answer_verified is false, otherwise omit this key,\n'
    '  "code_snippet_clean": a cleaned, properly formatted version of the code snippet '
    "(fix any mangled whitespace or concatenation artefacts from HTML scraping),\n"
    '  "difficulty_suggested": one of "easy", "medium", or "hard".\n\n'
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
        "subject": q.get("subject", ""),
        "text": q.get("text", ""),
        "options": options if isinstance(options, list) else [],
        "correct_answer": correct_answer,
        "correct_index": correct_index,
        "code_snippet": q.get("code_snippet", ""),
        "snippet_language": q.get("snippet_language", "text"),
    }


def enrich_code_questions(questions: list[dict], _retry: int = 0) -> dict[str, dict]:
    """
    Send a batch of code-snippet questions to OpenAI.
    Returns {str(id): result_dict}. Empty dict on unrecoverable failure.
    """
    formatted = [_format_question(q) for q in questions]
    try:
        response = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(formatted, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            timeout=90.0,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)
        results = parsed.get("results", [])
        return {str(r["id"]): r for r in results if "id" in r}

    except RateLimitError:
        if _retry < 3:
            wait = 10 * (2 ** _retry)
            log.warning("OpenAI rate limit — retrying in %ds (attempt %d/3)", wait, _retry + 1)
            time.sleep(wait)
            return enrich_code_questions(questions, _retry + 1)
        log.error("OpenAI rate limit exceeded after 3 retries for ids=%s", [q.get("id") for q in questions])
        return {}

    except Exception as e:
        log.error("OpenAI error (ids=%s): %s", [q.get("id") for q in questions], e)
        return {}