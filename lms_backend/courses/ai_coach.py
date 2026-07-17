"""Mendoura AI Coach -- a thin wrapper around the Anthropic Messages API.

The one network call (send_message) is isolated here so tests can mock it,
same pattern as bunny.create_video / paymob's request helpers.
"""
import anthropic
from django.conf import settings
from django.utils.translation import gettext as _

MODEL = 'claude-opus-4-8'

SYSTEM_PROMPT = (
    "You are Mendoura AI Coach, a friendly, encouraging, and elite academic "
    "tutor. Your goal is to help students understand complex topics, write "
    "summaries, build study schedules, and explain concepts simply. Keep "
    "answers well-formatted with markdown and friendly tones."
)


class AICoachError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.AI_API_KEY)


def send_message(history: list[dict]) -> str:
    """history is a list of {"role": "user"|"assistant", "content": str},
    oldest first. Returns the assistant's reply text."""
    if not is_configured():
        raise AICoachError(_('The AI Coach is not configured yet.'))

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
