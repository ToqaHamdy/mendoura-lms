"""Mendoura AI Coach -- a thin wrapper around the Anthropic Messages API.

The one network call (send_message) is isolated here so tests can mock it,
same pattern as bunny.create_video / paymob's request helpers.
"""
import anthropic
from django.conf import settings

MODEL = 'claude-opus-4-8'

SYSTEM_PROMPT = (
    "You are Mendoura AI Coach, a friendly, encouraging, and elite academic "
    "tutor. Your goal is to help students understand complex topics, write "
    "summaries, build study schedules, and explain concepts simply. Keep "
    "answers well-formatted with markdown and friendly tones."
)

# Sandbox Fallback Mode -- keeps the chat itself usable (demos, local dev,
# a deploy that hasn't got AI_API_KEY set yet) instead of locking the input
# and showing an admin-facing error. Clearly labeled as a preview in both
# the copy and the UI badge (see dashboard/ai_buddy.html) -- never presented
# as a real model response.
SANDBOX_PYTHON_GUIDE = """### \U0001f40d Your Python Learning Path (Sandbox Preview)

Here's a beginner-to-confident roadmap the real AI Coach can tailor to you once it's connected:

1. **Foundations (Week 1-2)**
   - Variables, data types, and basic input/output
   - Control flow: `if`/`elif`/`else`, `for`/`while` loops
   - Writing and calling your own functions

2. **Core Data Structures (Week 3)**
   - Lists, tuples, dictionaries, and sets
   - List comprehensions

3. **Intermediate Concepts (Week 4-5)**
   - Object-oriented programming: classes and inheritance
   - Error handling with `try`/`except`
   - Working with files and modules

4. **Build Something Real (Week 6+)**
   - A small script or CLI tool that solves a problem you actually have
   - Practice on real code, not just tutorials

```python
def greet(name: str) -> str:
    return f"Welcome to Python, {name}!"
```

*This is a sandbox preview -- once Mendoura AI Coach is fully connected, I'll tailor this plan to your exact course and pace.*"""

SANDBOX_STUDY_SCHEDULE = """### \U0001f4c5 Sample Weekly Study Schedule (Sandbox Preview)

| Day | Focus | Duration |
|---|---|---|
| Monday | Review lecture notes + flashcards | 45 min |
| Tuesday | Practice problems (weak topics) | 60 min |
| Wednesday | Watch next lecture + take notes | 45 min |
| Thursday | Practice problems (new topics) | 60 min |
| Friday | Light review + rest | 30 min |
| Saturday | Mock quiz / self-test | 60 min |
| Sunday | Rest, or a catch-up buffer | -- |

**Tips**
- Study in focused 25-45 minute blocks with short breaks in between.
- Spend 5-10 minutes revisiting yesterday's material before starting something new.
- Adjust this around your real exam dates once I'm fully connected!

*This is a sandbox preview -- once Mendoura AI Coach is fully connected, I'll build a schedule around your real courses and deadlines.*"""

SANDBOX_GENERIC_REPLY = (
    "**Welcome!** I'm currently running in **Mendoura Sandbox Mode** -- ask me about "
    "**Python**, **Study Schedules**, or **Course Pre-requisites** to see how I can guide you!"
)


class AICoachError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.AI_API_KEY)


def _sandbox_reply(history: list[dict]) -> str:
    """Keyword-matched canned reply used whenever AI_API_KEY isn't set."""
    last_user_text = next(
        (m.get('content', '') for m in reversed(history) if m.get('role') == 'user'), ''
    ).lower()

    if any(keyword in last_user_text for keyword in ('python', 'code', 'programming')):
        return SANDBOX_PYTHON_GUIDE
    if any(keyword in last_user_text for keyword in ('study', 'schedule', 'exam')):
        return SANDBOX_STUDY_SCHEDULE
    return SANDBOX_GENERIC_REPLY


def send_message(history: list[dict]) -> str:
    """history is a list of {"role": "user"|"assistant", "content": str},
    oldest first. Returns the assistant's reply text -- a canned Sandbox
    Mode reply when AI_API_KEY isn't configured, so the chat stays usable
    instead of erroring."""
    if not is_configured():
        return _sandbox_reply(history)

    client = anthropic.Anthropic(api_key=settings.AI_API_KEY)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            thinking={'type': 'adaptive'},
            messages=history,
        )
    except anthropic.APIError as exc:
        raise AICoachError(str(exc)) from exc

    return ''.join(block.text for block in response.content if block.type == 'text').strip()
