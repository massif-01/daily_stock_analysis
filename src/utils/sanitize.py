# -*- coding: utf-8 -*-
"""Shared text sanitizers for logs, diagnostics, and API payloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit


_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "sendkey",
    "token",
    "webhook",
}
_SENSITIVE_KEY_PHRASES = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "api_token",
    "apitoken",
    "auth_token",
    "authtoken",
    "authorization_header",
    "authorizationheader",
    "license_key",
    "licensekey",
    "private_key",
    "privatekey",
    "refresh_token",
    "refreshtoken",
    "secret_key",
    "secretkey",
    "session_token",
    "sessiontoken",
    "send_key",
    "sendkey",
    "webhook_url",
    "webhookurl",
}
_SENSITIVE_COMPACT_KEY_PHRASES = {
    phrase.replace("_", "") for phrase in _SENSITIVE_KEY_PHRASES
}
_SENSITIVE_COMPACT_KEY_PATTERN = re.compile(
    r"authorization|cookie|password|secret|sendkey|token(?!s)|webhook"
)
_URL_PATTERN = re.compile(r"https?://[^\s,;)\]}]+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\b(bearer\s+)[a-z0-9._\-:]+", re.IGNORECASE)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(token|secret|password|sendkey|api[_-]?key|apikey|api[_-]?token|auth[_-]?token|"
    r"access[_-]?token|refresh[_-]?token|session[_-]?token|license[_-]?key|private[_-]?key|"
    r"secret[_-]?key|webhook[_-]?url)([=:]\s*)[^\s,;&]+",
    re.IGNORECASE,
)
_TOKEN_LIKE_PATTERN = re.compile(
    r"\b(?:sk-[a-z0-9_\-]{16,}|xox[baprs]-[a-z0-9\-]{16,}|gh[pousr]_[a-z0-9_]{20,})\b",
    re.IGNORECASE,
)


def sanitize_diagnostic_text(text: Any, *, max_length: int = 300) -> str:
    """Redact common secrets and URLs from diagnostic text."""
    sanitized = str(text or "").strip()
    if not sanitized:
        return ""
    sanitized = re.sub(r"(?i)(bearer\s+)[a-z0-9._\-:]+", r"\1[REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)(token|secret|password|sendkey)([=:]\s*)[^\s,;&]+", r"\1\2[REDACTED]", sanitized)
    sanitized = re.sub(r"https?://[^\s]+", "[REDACTED_URL]", sanitized)
    return " ".join(sanitized.split())[:max_length]


def redact_sensitive_mapping(obj: Any) -> Any:
    """Recursively redact sensitive values from mappings by key name only.

    This helper intentionally does not inspect arbitrary string values. P1 only
    needs a deterministic serializer for AnalysisContextPack dictionaries.
    """
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            if _is_sensitive_mapping_key(key):
                redacted[key] = _REDACTED
            else:
                redacted[key] = redact_sensitive_mapping(value)
        return redacted
    if isinstance(obj, list):
        return [redact_sensitive_mapping(item) for item in obj]
    return obj


def sanitize_decision_signal_text(text: Any) -> str:
    """Redact obvious secrets from persisted decision-signal text without truncating."""
    sanitized = str(text or "").strip()
    if not sanitized:
        return ""
    sanitized = _URL_PATTERN.sub(_redact_sensitive_url_match, sanitized)
    sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _TOKEN_LIKE_PATTERN.sub("[REDACTED]", sanitized)
    return " ".join(sanitized.split())


def sanitize_decision_signal_payload(obj: Any) -> Any:
    """Redact decision-signal JSON payloads by sensitive keys and string values."""
    redacted = redact_sensitive_mapping(obj)
    return _sanitize_decision_signal_payload_values(redacted)


def _sanitize_decision_signal_payload_values(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: _sanitize_decision_signal_payload_values(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_sanitize_decision_signal_payload_values(item) for item in obj]
    if isinstance(obj, str):
        return sanitize_decision_signal_text(obj)
    return obj


def _redact_sensitive_url_match(match: re.Match[str]) -> str:
    url = match.group(0)
    if _is_sensitive_url(url):
        return "[REDACTED_URL]"
    return url


def _is_sensitive_url(url: str) -> bool:
    if _TOKEN_LIKE_PATTERN.search(url):
        return True
    try:
        query_items = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return False
    return any(_is_sensitive_mapping_key(key) for key, _value in query_items)


def _is_sensitive_mapping_key(key: Any) -> bool:
    key_text = str(key or "").strip()
    if not key_text:
        return False
    parts = _mapping_key_parts(key_text)
    if _has_sensitive_phrase("_".join(parts)):
        return True
    return bool(set(parts) & _SENSITIVE_KEY_PARTS)


def _has_sensitive_phrase(normalized_key: str) -> bool:
    padded_key = f"_{normalized_key}_"
    if any(f"_{phrase}_" in padded_key for phrase in _SENSITIVE_KEY_PHRASES):
        return True
    compact_key = normalized_key.replace("_", "")
    if any(phrase in compact_key for phrase in _SENSITIVE_COMPACT_KEY_PHRASES):
        return True
    return bool(_SENSITIVE_COMPACT_KEY_PATTERN.search(compact_key))


def _mapping_key_parts(key_text: str) -> list[str]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key_text)
    return [
        part.lower()
        for part in re.split(r"[^A-Za-z0-9]+", split_camel)
        if part
    ]
