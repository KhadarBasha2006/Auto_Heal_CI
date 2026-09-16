"""Secret redaction.

The user hands us a GitHub token so the agent can push on their behalf. That
token must never reach a log line, an SSE frame, an error message, or a stored
job record. Every string that leaves the agent is funnelled through `scrub`.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "***REDACTED***"

# Well-known credential shapes, matched even if we were never told the value.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # GitHub tokens: ghp_, gho_, ghu_, ghs_, ghr_, github_pat_
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    # Anthropic keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    # Generic bearer headers
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|token)\s+)\S+"),
    # Credentials embedded in a clone URL: https://user:token@host/...
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),
)


class SecretRegistry:
    """Holds literal secret values seen at runtime so they can be scrubbed."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, secret: str | None) -> None:
        # Very short strings would scrub harmless text, so require some length.
        if secret and len(secret) >= 8:
            self._secrets.add(secret)

    def scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, MASK)
        return text

    def clear(self) -> None:
        self._secrets.clear()


_registry = SecretRegistry()


def register_secret(secret: str | None) -> None:
    """Remember a literal secret so later output can be scrubbed of it."""
    _registry.register(secret)


def scrub(text: Any) -> Any:
    """Remove known secrets and credential-shaped substrings from `text`.

    Non-string scalars pass through untouched; dicts and lists are scrubbed
    recursively so whole payloads can be sanitised in one call.
    """
    if text is None:
        return None
    if isinstance(text, str):
        cleaned = _registry.scrub(text)
        for pattern in _PATTERNS:
            if pattern.groups:
                cleaned = pattern.sub(lambda m: m.group(1) + MASK, cleaned)
            else:
                cleaned = pattern.sub(MASK, cleaned)
        return cleaned
    if isinstance(text, dict):
        return {key: scrub(value) for key, value in text.items()}
    if isinstance(text, (list, tuple)):
        return [scrub(item) for item in text]
    return text


def clear_secrets() -> None:
    """Forget every registered secret. Used by tests."""
    _registry.clear()
