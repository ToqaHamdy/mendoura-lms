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

# General-Purpose Sandbox AI Engine -- keeps the chat itself usable across
# any domain (demos, local dev, a deploy that hasn't got AI_API_KEY set yet)
# instead of locking the input and showing an admin-facing error. Clearly
# labeled as a preview in both the copy and the UI badge (see
# dashboard/ai_buddy.html) -- never presented as a real model response.
SANDBOX_TECH_GUIDE = """### \U0001f4bb Modern Software Engineering Best Practices (Sandbox Preview)

Here's a foundation the real AI Coach can tailor to your exact stack once it's connected:

1. **Write Readable Code First**
   - Clear naming beats clever naming
   - Small, single-purpose functions and files

2. **Version Control Discipline**
   - Small, focused commits with clear messages
   - One feature per branch, reviewed before merging

3. **Test as You Go**
   - Unit tests for logic, integration tests for full flows
   - A bug caught by a test today is an outage avoided in production

4. **Handle Errors Deliberately**
   - Fail loudly in development, gracefully in production
   - Never swallow exceptions silently -- log or re-raise

```python
def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
```

```javascript
function divide(a, b) {
  if (b === 0) throw new Error("Cannot divide by zero");
  return a / b;
}
```

*This is a sandbox preview -- once Mendoura AI Coach is fully connected, I'll tailor this to the language, framework, or bug you're actually working on.*"""

SANDBOX_BUSINESS_FRAMEWORK = """### \U0001f4c8 Elite Entrepreneurship Framework (Sandbox Preview)

| Stage | Focus | Key Question |
|---|---|---|
| Validate | Talk to real customers | Does this problem actually hurt? |
| Build | Ship a minimum viable version | What's the smallest thing that proves the idea? |
| Launch | Get it in front of people | Who are your first 10 customers? |
| Measure | Track what matters | Are people coming back? |
| Scale | Double down on what works | What's your highest-leverage channel? |

**Marketing & Sales Fundamentals**
- Know your ideal customer better than they know themselves
- Sell the transformation, not the feature list
- Profit follows retention -- a repeat customer is worth more than a new one

*This is a sandbox preview -- once Mendoura AI Coach is fully connected, I'll tailor this framework to your actual project and market.*"""

SANDBOX_LANGUAGE_ROADMAP = """### \U0001f5e3️ Your Language Learning Roadmap (Sandbox Preview)

1. **Absolute Basics (Week 1-2)**
   - Greetings, numbers, and common phrases
   - 15-20 minutes of daily listening practice

2. **Everyday Conversation (Week 3-4)**
   - Ordering food, asking directions, small talk
   - Start speaking out loud, even alone

3. **Grammar Foundations (Week 5-6)**
   - Core verb tenses and sentence structure
   - Read short, simple texts daily

4. **Real Immersion (Week 7+)**
   - Watch shows or podcasts in the target language
   - Find a conversation partner or tutor

**Quick tip:** Whether it's Arabic, English, or translation practice you need, consistency beats intensity -- 20 minutes daily outperforms 3 hours once a week.

*This is a sandbox preview -- once Mendoura AI Coach is fully connected, I'll tailor this roadmap to the language and level you're actually at.*"""

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

SANDBOX_GENERAL_REPLY = (
    "**Hello!** I am your **Mendoura General AI Assistant** running in Sandbox mode. "
    "I can map out study strategies, break down complex concepts, or draft learning "
    "paths for you! What topic are we exploring today?"
)


class AICoachError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.AI_API_KEY)


def _sandbox_reply(history: list[dict]) -> str:
    """Keyword-matched canned reply used whenever AI_API_KEY isn't set. Checked
    most-specific-first; a message that matches nothing (or only the generic
    chit-chat openers -- "hi", "help", "explain", "why", "what is", ...) gets
    the same warm general-assistant reply either way."""
    last_user_text = next(
        (m.get('content', '') for m in reversed(history) if m.get('role') == 'user'), ''
    ).lower()

    if any(keyword in last_user_text for keyword in ('python', 'js', 'javascript', 'html', 'code', 'bug', 'web')):
        return SANDBOX_TECH_GUIDE
    if any(keyword in last_user_text for keyword in ('marketing', 'business', 'sales', 'profit', 'project')):
        return SANDBOX_BUSINESS_FRAMEWORK
    if any(keyword in last_user_text for keyword in ('english', 'arabic', 'translation', 'learn')):
        return SANDBOX_LANGUAGE_ROADMAP
    if any(keyword in last_user_text for keyword in ('study', 'schedule', 'exam')):
        return SANDBOX_STUDY_SCHEDULE
    return SANDBOX_GENERAL_REPLY


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
