"""
Prompts for the Operating Systems topic-classification pass (v2).

Separate from explanation_prompts so each can evolve independently.
"""

TOPIC_PROMPT_VERSION = "os-topic-v1"

CANONICAL_TOPICS = [
    "Process Management",
    "Memory Management",
    "Concurrency and Synchronization",
    "File Systems",
    "I/O Systems and Device Management",
    "Protection and Security",
    "Virtualization",
    "Networking",
]

_SYSTEM_PROMPT = """\
You are a computer science professor classifying Operating Systems exam questions \
into exactly one canonical topic from the list below.

Canonical topics (pick exactly one):
  1. Process Management          — processes, threads, CPU scheduling, deadlocks, IPC, context switching, RTOS
  2. Memory Management           — paging, segmentation, swapping, virtual memory, page replacement, frame allocation, thrashing
  3. Concurrency and Synchronization — semaphores, mutexes, monitors, critical sections, atomic transactions, classic sync problems (producer-consumer, dining philosophers, readers-writers)
  4. File Systems                — file system structure, allocation methods, directory organisation, access methods, free-space management, file system recovery
  5. I/O Systems and Device Management — I/O interfaces, device drivers, kernel I/O subsystem, disk scheduling, RAID, mass storage, multimedia I/O
  6. Protection and Security     — access matrix, protection rings, memory protection, authentication, cryptography, intrusion detection, system threats
  7. Virtualization              — virtual machines, hypervisors, containers, OS-level virtualisation, cloud infrastructure
  8. Networking                  — distributed OS, network file systems, RPC, network topology, distributed synchronisation, distributed coordination

Rules:
- Pick the single best-fit topic. If a question touches two topics, pick the one that is *most directly tested*.
- Return ONLY a JSON object with a single key "topic" whose value is the exact string from the list above.
- No markdown fences, no commentary, no extra keys.
"""

_USER_PROMPT_TEMPLATE = """\
Question: {text}

Options:
{options_formatted}

Correct answer: {correct_answer}

Explanation: {explanation}

Return JSON:
{{"topic": "<one of the 8 canonical topics>"}}
"""


def build_topic_prompt(question: dict) -> tuple[str, str]:
    """
    Return (system_prompt, user_prompt) for the topic-classification call.

    Expects question dict keys: text, options (list), correct_index, explanation.
    """
    options = question.get("options") or []
    correct_index = question.get("correct_index")

    try:
        correct_answer = options[int(correct_index)]
    except (IndexError, TypeError, ValueError):
        correct_answer = "[unknown]"

    options_formatted = "\n".join(f"  - {opt}" for opt in options) if options else "  [no options]"
    explanation = (question.get("explanation") or "").strip() or "[not available]"

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        text=question.get("text", ""),
        options_formatted=options_formatted,
        correct_answer=correct_answer,
        explanation=explanation,
    )

    return _SYSTEM_PROMPT, user_prompt
