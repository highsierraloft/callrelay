"""Keypad navigation for an existing call; never originates or transfers a call."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

MAX_KEYPAD_ACTIONS = 24
MENU_LISTEN_SECONDS = 60

MENU_INSTRUCTIONS = (
    "\nAutomated phone menus: listen to the available options and choose the department or "
    "extension that serves the approved task. Use send_dtmf to actually press the stated "
    "digits; saying a number aloud does not press it. Send only the choice for the current "
    "menu. A menu-tool turn must contain ONLY the tool call: no content text, analysis, "
    "explanation, or spoken acknowledgement before or after it. Then listen for the next "
    "menu or person. Use wait_for_phone_menu without spoken "
    "text when a menu is still listing options, asks you to hold, or is transferring you. "
    "Do not talk over recordings, thank a recording, announce keypad actions, or mistake "
    "an automated goodbye/thank-you for completion of the task. If the best choice is "
    "already clear, press it instead of waiting for an operator unnecessarily. If unclear, "
    "listen for the full menu or use an explicitly offered operator option; do not guess "
    "unannounced keys. When a person answers, introduce the approved task and resume a "
    "normal conversation. A hold message or menu interruption is not a request to end. "
    "Keypad entry does not grant new authority: use only supplied, approved information, "
    "never guess credentials, authorize purchases, or accept new commitments outside the "
    "brief. Send an extension as digits, including # only if requested. Never send an "
    "authentication code obtained from another source without the owner's approval. "
    "An accepted keypad operation confirms dispatch, not that the remote menu accepted "
    "it. After an uncertain result, listen; never blindly retry. Retry only if a fresh "
    "menu explicitly requests the choice again. If the same menu loops twice without "
    "progress, use an offered operator option or end politely with finish_call; never "
    "invent success. Stay within the call's existing time budget.\n"
)


def is_phone_menu_prompt(text: str) -> bool:
    """Limit speech buffering to explicit keypad/hold cues, not ordinary replies."""
    return bool(
        re.search(
            r"\b(?:press|dial|enter)\s+(?:the\s+)?(?:[0-9]|one|two|three|four|five|six|seven|eight|nine|zero|star|hash|pound|extension)|"
            r"\b(?:marque|marca|pulse|pulsa|teclee|teclea)\b|"
            r"\b(?:нажмите|нажми|наберите|введите|натисніть|наберіть|введіть)\b|"
            r"\b(?:please\s+hold|stay\s+on\s+the\s+line|listen\s+to\s+(?:all|the)\s+(?:options|menu)|"
            r"manténgase\s+a\s+la\s+espera|le\s+paso\s+con|le\s+pasamos\s+con|"
            r"оставайтесь\s+на\s+линии|ожидайте\s+ответа|залишайтеся\s+на\s+лінії|очікуйте\s+відповіді)",
            text,
            re.IGNORECASE,
        )
    )


def is_hold_prompt(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:please\s+hold|stay\s+on\s+the\s+line|"
            r"manténgase\s+a\s+la\s+espera|le\s+paso\s+con|le\s+pasamos\s+con|"
            r"оставайтесь\s+на\s+линии|ожидайте\s+ответа|залишайтеся\s+на\s+лінії|очікуйте\s+відповіді)",
            text,
            re.IGNORECASE,
        )
    ) and not re.search(
        r"\b(?:press|dial|enter|marque|pulse|teclee|нажмите|наберите|натисніть|наберіть)\b",
        text,
        re.IGNORECASE,
    )


def validate_digits(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9*#]{1,16}", value):
        raise ValueError("Supply 1–16 keypad digits: 0–9, * or #")
    return value


def menu_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "send_dtmf",
                "description": "Press keypad digits on this connected call to navigate the current automated menu or enter an approved extension. No spoken acknowledgement. Listen after sending. Does not dial a new number or transfer the call.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "digits": {
                            "type": "string",
                            "pattern": "^[0-9*#]{1,16}$",
                            "description": "Only the digits requested by the current menu, such as 2 or 123#. No words, spaces, pause codes or phone-number prefix.",
                        }
                    },
                    "required": ["digits"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wait_for_phone_menu",
                "description": "Listen silently while an automated menu continues, a transfer connects, or you are on hold. Use without spoken text. Audio recognition stays active; the next recipient speech can resume the conversation. The existing call time limit still applies.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]


class PhoneMenu:
    """Serialize keypad requests and retain uncertain outcomes instead of retrying."""

    def __init__(self, send: Callable[[str], Awaitable[None]] | None) -> None:
        self._send = send
        self._lock = asyncio.Lock()
        self._receipts: dict[str, tuple[str, dict[str, Any]]] = {}
        self._last_revision = -1
        self._attempts = 0

    async def press(self, tool_id: str, digits: Any, revision: int) -> dict[str, Any]:
        digits = validate_digits(digits)
        async with self._lock:
            if tool_id in self._receipts:
                previous, cached_result = self._receipts[tool_id]
                if previous != digits:
                    raise ValueError("This keypad request already has a different digit sequence")
                return {**cached_result, "duplicate": True}
            if revision <= self._last_revision:
                return {
                    "status": "not_sent",
                    "reason": "Listen for the next menu before pressing again",
                }
            if self._attempts >= MAX_KEYPAD_ACTIONS:
                return {
                    "status": "not_sent",
                    "reason": "Keypad navigation limit reached; end this unsuccessful attempt",
                }
            self._attempts += 1
            self._last_revision = revision
            # Write before dispatch. Cancellation/timeout cannot authorize another send.
            result: dict[str, Any] = {
                "status": "unknown",
                "reason": "Keypad outcome is uncertain. Listen; do not retry without a fresh menu request.",
            }
            self._receipts[tool_id] = (digits, result)
            if self._send is None:
                result = {"status": "simulated", "digits": digits, "sent_to_phone": False}
            else:
                try:
                    await self._send(digits)
                except ValueError:
                    result = {
                        "status": "not_sent",
                        "reason": "This call is no longer ready for keypad input",
                    }
                except Exception:
                    # Provider details and possible credentials must not reach the model.
                    pass
                else:
                    result = {
                        "status": "accepted",
                        "digits": digits,
                        "menu_acceptance": "unconfirmed",
                    }
            self._receipts[tool_id] = (digits, result)
            return result
