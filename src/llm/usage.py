# -*- coding: utf-8 -*-
"""LLM usage normalization and prompt-message HMAC telemetry."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

PROVIDER_USAGE_SCHEMA_NAME = "provider_usage_v1"
PROVIDER_USAGE_SCHEMA_VERSION = "2026-06-10"
PROVIDER_USAGE_MAX_SIZE_BYTES = 4096

DEFAULT_HMAC_DOMAIN = "prompt_message"
DEFAULT_HASH_SCOPE = "deployment"
DEFAULT_HMAC_KEY_VERSION = "local-v1"

_HMAC_SECRET_CACHE: Optional[bytes] = None
_DROP_RAW_USAGE_VALUE = object()
_OPENAI_LITELLM_PROVIDER = "openai"
_OPENAI_COMPATIBLE_PROVIDER = "openai_compatible"
_ALLOWED_RAW_USAGE_SCALAR_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_token_count",
    "input_token_count",
    "candidates_token_count",
    "output_token_count",
    "total_token_count",
    "cached_tokens",
    "cached_content_token_count",
    "cache_read_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "estimated_prefix_tokens",
    "tokenizer_name",
    "tokenizer_version",
}
_ALLOWED_RAW_USAGE_DETAIL_KEYS = {
    "prompt_tokens_details",
    "completion_tokens_details",
    "input_tokens_details",
    "output_tokens_details",
    "input_token_details",
    "output_token_details",
}
_ALLOWED_RAW_USAGE_DETAIL_SCALAR_KEYS = {
    "accepted_prediction_tokens",
    "audio_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cached_tokens",
    "image_tokens",
    "reasoning_tokens",
    "rejected_prediction_tokens",
    "text_tokens",
}
_SENSITIVE_VALUE_KEYS = {
    "access_token",
    "api_key",
    "api_token",
    "auth_token",
    "authorization",
    "key",
    "private_key",
    "refresh_token",
    "secret_key",
    "session_token",
    "token",
}
_SENSITIVE_COMPACT_VALUE_KEYS = {key.replace("_", "") for key in _SENSITIVE_VALUE_KEYS}
_BEARER_VALUE_RE = re.compile(
    r"(?i)\b(authorization\s*:\s*bearer|bearer)(\s+)[A-Za-z0-9._~+/=-]+"
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b("
    r"access_token|api_key|api_token|auth_token|authorization|key|private_key|"
    r"refresh_token|secret_key|session_token|token"
    r")(\s*[=:]\s*)[^\s,;&]+"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_REDACTED_URL = "[REDACTED_URL]"
_PROVIDER_USAGE_SIGNAL_TOKEN_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_token_count",
    "input_token_count",
    "candidates_token_count",
    "output_token_count",
    "total_token_count",
    "normalized_prompt_tokens",
    "normalized_completion_tokens",
    "normalized_total_tokens",
    "provider_reported_prompt_tokens",
)


def extract_usage_payload(response: Any) -> Any:
    """Return the provider usage payload from a LiteLLM response or chunk."""
    if response is None:
        return None
    if isinstance(response, Mapping):
        return response.get("usage") or response.get("usage_metadata")
    return getattr(response, "usage", None) or getattr(response, "usage_metadata", None)


def has_provider_usage_payload(usage: Mapping[str, Any] | None) -> bool:
    """Return whether a usage dict represents provider-reported token usage."""
    if not usage:
        return False

    provider_usage_json = usage.get("provider_usage_json")
    if isinstance(provider_usage_json, str):
        if provider_usage_json.strip():
            return True
    elif provider_usage_json:
        return True

    for key in _PROVIDER_USAGE_SIGNAL_TOKEN_KEYS:
        if key in usage and _usage_value_is_nonzero(usage.get(key)):
            return True
    return False


def normalize_litellm_usage(
    usage_obj: Any,
    *,
    model: str = "",
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize provider usage without changing request or response behavior."""
    usage = _to_plain(usage_obj)
    provider_name = _infer_provider(model, provider)
    raw_json = _safe_provider_usage_json(usage)
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    result: Dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "provider_usage_json": raw_json,
        "provider_usage_schema_name": PROVIDER_USAGE_SCHEMA_NAME if raw_json else None,
        "provider_usage_schema_version": PROVIDER_USAGE_SCHEMA_VERSION if raw_json else None,
        "provider_usage_observed_at": observed_at if raw_json else None,
        "normalized_prompt_tokens": None,
        "normalized_completion_tokens": None,
        "normalized_total_tokens": None,
        "normalized_cache_read_tokens": None,
        "normalized_cache_write_tokens": None,
        "normalized_cache_miss_tokens": None,
        "normalized_uncached_input_tokens": None,
        "normalized_cache_eligible_input_tokens": None,
        "normalized_cache_hit_ratio": None,
        "normalized_cache_write_ratio": None,
        "cache_capability": "unknown",
        "cache_eligibility": "unknown",
        "cache_observation": "no_usage" if not usage else "unknown",
        "estimated_prefix_tokens": None,
        "provider_reported_prompt_tokens": None,
        "provider_reported_cached_tokens": None,
        "provider_min_cache_tokens": None,
        "eligibility_confidence": "unknown",
        "tokenizer_name": None,
        "tokenizer_version": None,
    }

    if not usage:
        return result

    prompt_tokens = _first_int(
        usage,
        "prompt_tokens",
        "input_tokens",
        "prompt_token_count",
        "input_token_count",
    )
    completion_tokens = _first_int(
        usage,
        "completion_tokens",
        "output_tokens",
        "candidates_token_count",
        "output_token_count",
    )
    total_tokens = _first_int(usage, "total_tokens", "total_token_count")

    cache_read: Optional[int] = None
    cache_write: Optional[int] = None
    cache_miss: Optional[int] = None
    provider_min_cache_tokens: Optional[int] = None
    cache_field_observed = False
    capability = "unknown"

    if provider_name == "openai":
        cached = _nested_int(usage, ("prompt_tokens_details", "cached_tokens"))
        if cached is not None:
            cache_read = cached
            cache_field_observed = True
            capability = "supported"
        provider_min_cache_tokens = 1024
    elif provider_name in {"glm", _OPENAI_COMPATIBLE_PROVIDER}:
        cached = _nested_int(usage, ("prompt_tokens_details", "cached_tokens"))
        if cached is not None:
            cache_read = cached
            cache_field_observed = True
            capability = "supported"
    elif provider_name == "anthropic":
        read_tokens = _first_int(usage, "cache_read_input_tokens")
        creation_tokens = _first_int(usage, "cache_creation_input_tokens")
        input_tokens = _first_int(usage, "input_tokens")
        if read_tokens is not None or creation_tokens is not None:
            cache_read = read_tokens or 0
            cache_write = creation_tokens or 0
            cache_field_observed = True
            capability = "supported"
            prompt_tokens = (input_tokens or 0) + (cache_read or 0) + (cache_write or 0)
            result["normalized_uncached_input_tokens"] = input_tokens
    elif provider_name == "gemini":
        cached = _first_int(usage, "cached_content_token_count", "cache_read_tokens")
        if cached is not None:
            cache_read = cached
            cache_field_observed = True
            capability = "supported"
    elif provider_name == "deepseek":
        hit_tokens = _first_int(usage, "prompt_cache_hit_tokens")
        miss_tokens = _first_int(usage, "prompt_cache_miss_tokens")
        if hit_tokens is not None or miss_tokens is not None:
            cache_read = hit_tokens or 0
            cache_miss = miss_tokens or 0
            cache_field_observed = True
            capability = "supported"
            prompt_tokens = (cache_read or 0) + (cache_miss or 0)
            result["normalized_uncached_input_tokens"] = cache_miss
    elif provider_name == "stepfun":
        cached = _first_int(usage, "cached_tokens")
        if cached is not None:
            cache_read = cached
            cache_field_observed = True
            capability = "supported"

    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    result["prompt_tokens"] = prompt_tokens or 0
    result["completion_tokens"] = completion_tokens or 0
    result["total_tokens"] = total_tokens or 0
    result["normalized_prompt_tokens"] = prompt_tokens
    result["normalized_completion_tokens"] = completion_tokens
    result["normalized_total_tokens"] = total_tokens
    result["provider_reported_prompt_tokens"] = prompt_tokens
    result["provider_reported_cached_tokens"] = cache_read
    result["provider_min_cache_tokens"] = provider_min_cache_tokens

    result["cache_capability"] = capability

    eligible_tokens = _eligible_input_tokens(prompt_tokens, provider_min_cache_tokens, capability)
    invalid_cache_usage = _has_invalid_cache_usage(
        prompt_tokens=prompt_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        cache_miss=cache_miss,
        capability=capability,
    )
    if invalid_cache_usage:
        result["cache_eligibility"] = "unknown"
        result["cache_observation"] = "invalid_provider_usage"
        result["eligibility_confidence"] = "invalid"
        result["normalized_uncached_input_tokens"] = None
        return result

    result["normalized_cache_read_tokens"] = cache_read
    result["normalized_cache_write_tokens"] = cache_write
    result["normalized_cache_miss_tokens"] = cache_miss

    if result["normalized_uncached_input_tokens"] is None and prompt_tokens is not None and cache_read is not None:
        result["normalized_uncached_input_tokens"] = max(prompt_tokens - cache_read - (cache_write or 0), 0)

    result["normalized_cache_eligible_input_tokens"] = eligible_tokens
    result["cache_eligibility"] = _cache_eligibility(prompt_tokens, provider_min_cache_tokens, capability)
    result["eligibility_confidence"] = "exact" if prompt_tokens is not None else "unknown"
    result["cache_observation"] = _cache_observation(
        has_usage=True,
        cache_field_observed=cache_field_observed,
        cache_read=cache_read,
        cache_write=cache_write,
        cache_miss=cache_miss,
        eligible_tokens=eligible_tokens,
    )
    result["normalized_cache_hit_ratio"] = _ratio(cache_read, eligible_tokens)
    result["normalized_cache_write_ratio"] = _ratio(cache_write, eligible_tokens)
    return result


def attach_message_hmacs(
    usage: Dict[str, Any],
    messages: Optional[Sequence[Mapping[str, Any]]],
    *,
    hash_scope: str = DEFAULT_HASH_SCOPE,
) -> Dict[str, Any]:
    """Attach message-level HMAC fields without storing prompt content."""
    result = dict(usage or {})
    hmac_fields = build_message_hmacs(messages, hash_scope=hash_scope)
    result.update(hmac_fields)
    return result


def build_message_hmacs(
    messages: Optional[Sequence[Mapping[str, Any]]],
    *,
    hash_scope: str = DEFAULT_HASH_SCOPE,
) -> Dict[str, Any]:
    """Return HMAC-SHA256 fingerprints for full/system/user messages."""
    base = {
        "messages_hmac": None,
        "system_message_hmac": None,
        "user_message_hmac": None,
        "hmac_key_version": None,
        "hmac_domain": DEFAULT_HMAC_DOMAIN,
        "hash_scope": hash_scope,
    }
    if not messages:
        return base
    secret = _load_usage_hmac_secret()
    if not secret:
        return base
    key_version = os.getenv("LLM_USAGE_HMAC_KEY_VERSION", DEFAULT_HMAC_KEY_VERSION).strip() or DEFAULT_HMAC_KEY_VERSION
    normalized_messages = [_message_for_hmac(message) for message in messages]
    base["messages_hmac"] = _hmac_json(secret, normalized_messages)
    base["system_message_hmac"] = _role_hmac(secret, normalized_messages, "system")
    base["user_message_hmac"] = _role_hmac(secret, normalized_messages, "user")
    base["hmac_key_version"] = key_version
    return base


def _role_hmac(secret: bytes, messages: Sequence[Mapping[str, Any]], role: str) -> Optional[str]:
    role_messages = [message for message in messages if message.get("role") == role]
    if not role_messages:
        return None
    return _hmac_json(secret, role_messages)


def _hmac_json(secret: bytes, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _message_for_hmac(message: Mapping[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in message.items():
        key_text = str(key)
        if key_text.startswith("_trace_"):
            continue
        normalized[key_text] = str(value or "") if key_text == "role" else _plain_value(value)
    normalized.setdefault("role", "")
    return normalized


def _load_usage_hmac_secret() -> Optional[bytes]:
    global _HMAC_SECRET_CACHE
    env_secret = os.getenv("LLM_USAGE_HMAC_SECRET")
    if env_secret:
        return env_secret.encode("utf-8")
    if _HMAC_SECRET_CACHE is not None:
        return _HMAC_SECRET_CACHE

    secret_path = _usage_hmac_secret_path()
    try:
        if secret_path.exists():
            data = secret_path.read_bytes()
            if data:
                _HMAC_SECRET_CACHE = data
                return data
            logger.warning("Invalid empty .llm_usage_hmac_secret, regenerating")
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        new_secret = secrets.token_bytes(32)
        try:
            with open(secret_path, "xb") as f:
                f.write(new_secret)
            secret_path.chmod(0o600)
        except FileExistsError:
            data = secret_path.read_bytes()
            if data:
                _HMAC_SECRET_CACHE = data
                return data
            secret_path.write_bytes(new_secret)
            secret_path.chmod(0o600)
        _HMAC_SECRET_CACHE = new_secret
        return new_secret
    except OSError as exc:
        logger.warning("[LLM usage] failed to load HMAC secret: %s", exc)
        return None


def _usage_hmac_secret_path() -> Path:
    db_path = os.getenv("DATABASE_PATH", "./data/stock_analysis.db")
    return Path(db_path).resolve().parent / ".llm_usage_hmac_secret"


def _to_plain(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(k): _plain_value(v) for k, v in value.items()}
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                dumped = method()
                if isinstance(dumped, Mapping):
                    return {str(k): _plain_value(v) for k, v in dumped.items()}
            except Exception:
                pass
    result: Dict[str, Any] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "cached_tokens",
        "cached_content_token_count",
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "prompt_tokens_details",
    ):
        if hasattr(value, key):
            result[key] = _plain_value(getattr(value, key))
    return result


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                dumped = method()
                return _plain_value(dumped)
            except Exception:
                pass
    return str(value)


def _usage_value_is_nonzero(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        try:
            return float(text) != 0
        except ValueError:
            return True
    return True


def _safe_provider_usage_json(usage: Mapping[str, Any]) -> Optional[str]:
    if not usage:
        return None
    sanitized = _sanitize_raw_usage(dict(usage))
    if not sanitized:
        return None
    payload = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    size = len(payload.encode("utf-8"))
    if size <= PROVIDER_USAGE_MAX_SIZE_BYTES:
        return payload
    marker = {
        "_truncated": True,
        "_original_size_bytes": size,
        "prompt_tokens": _first_int(sanitized, "prompt_tokens", "input_tokens", "prompt_token_count"),
        "completion_tokens": _first_int(
            sanitized,
            "completion_tokens",
            "output_tokens",
            "candidates_token_count",
        ),
        "total_tokens": _first_int(sanitized, "total_tokens", "total_token_count"),
    }
    return json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sanitize_raw_usage(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _ALLOWED_RAW_USAGE_DETAIL_KEYS and isinstance(item, Mapping):
                detail = _sanitize_raw_usage_detail(item)
                if detail:
                    result[key_text] = detail
                continue
            if key_text not in _ALLOWED_RAW_USAGE_SCALAR_KEYS:
                continue
            sanitized = _sanitize_raw_usage_scalar(item)
            if sanitized is not _DROP_RAW_USAGE_VALUE:
                result[key_text] = sanitized
        return result
    sanitized_value = _sanitize_raw_usage_scalar(value)
    return None if sanitized_value is _DROP_RAW_USAGE_VALUE else sanitized_value


def _sanitize_raw_usage_detail(value: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text not in _ALLOWED_RAW_USAGE_DETAIL_SCALAR_KEYS:
            continue
        sanitized = _sanitize_raw_usage_scalar(item)
        if sanitized is not _DROP_RAW_USAGE_VALUE:
            result[key_text] = sanitized
    return result


def _sanitize_raw_usage_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_raw_usage_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _DROP_RAW_USAGE_VALUE


def _sanitize_raw_usage_text(text: str) -> str:
    sanitized = _BEARER_VALUE_RE.sub(r"\1\2[REDACTED]", text)
    sanitized = _ASSIGNMENT_SECRET_RE.sub(r"\1\2[REDACTED]", sanitized)
    return _URL_RE.sub(_sanitize_url_match, sanitized)


def _sanitize_url_match(match: re.Match[str]) -> str:
    url = match.group(0)
    trailing = ""
    while url and url[-1] in ".,);":
        trailing = url[-1] + trailing
        url = url[:-1]
    try:
        parts = urlsplit(url)
    except ValueError:
        return match.group(0)

    if _is_webhook_url(parts.netloc, parts.path):
        return _REDACTED_URL + trailing

    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"[REDACTED]@{netloc.rsplit('@', 1)[1]}"

    query = _sanitize_query_string(parts.query)
    fragment = _sanitize_fragment(parts.fragment)
    redacted = urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    return redacted + trailing


def _is_webhook_url(netloc: str, path: str) -> bool:
    host = str(netloc or "").rsplit("@", 1)[-1].split(":", 1)[0].lower().strip(".")
    normalized_path = f"/{str(path or '').lstrip('/').lower()}"

    if host == "hooks.slack.com" and normalized_path.startswith("/services/"):
        return True
    if host in {"discord.com", "discordapp.com"} and "/api/webhooks/" in normalized_path:
        return True
    if host in {"open.feishu.cn", "open.larksuite.com"}:
        return "/open-apis/bot/" in normalized_path and "/hook/" in normalized_path
    if host == "qyapi.weixin.qq.com" and normalized_path.startswith("/cgi-bin/webhook/send"):
        return True
    if host == "oapi.dingtalk.com" and normalized_path.startswith("/robot/send"):
        return True
    if host in {"sctapi.ftqq.com", "sc.ftqq.com"}:
        return True
    return host.startswith("hooks.")


def _sanitize_query_string(query: str) -> str:
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        return query
    sanitized_pairs = [
        (key, "[REDACTED]" if _is_sensitive_value_key(key) else value)
        for key, value in pairs
    ]
    return urlencode(sanitized_pairs, doseq=True)


def _sanitize_fragment(fragment: str) -> str:
    if not fragment:
        return fragment
    if "=" in fragment:
        return _sanitize_query_string(fragment)
    return _ASSIGNMENT_SECRET_RE.sub(r"\1\2[REDACTED]", fragment)


def _is_sensitive_value_key(key: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if normalized in _SENSITIVE_VALUE_KEYS:
        return True
    compact = normalized.replace("_", "")
    return compact in _SENSITIVE_COMPACT_VALUE_KEYS


def _infer_provider(model: str, provider: Optional[str]) -> str:
    normalized_model = (model or "").strip().lower()
    normalized_provider = (provider or "").strip().lower()
    route_text = f"{normalized_provider} {normalized_model}"

    if normalized_model.startswith("openai/~") or "openrouter" in route_text:
        return "openrouter"
    if normalized_provider in {"zhipu", "bigmodel", "glm"}:
        return "glm"
    if normalized_provider in {"anthropic", "gemini", "deepseek", "stepfun"}:
        return normalized_provider
    if normalized_provider == _OPENAI_LITELLM_PROVIDER:
        return "openai" if _is_native_openai_model(normalized_model) else _OPENAI_COMPATIBLE_PROVIDER
    if normalized_model.startswith("openai/"):
        return "openai" if _is_native_openai_model(normalized_model) else _OPENAI_COMPATIBLE_PROVIDER

    if _is_glm_model(normalized_model):
        return "glm"
    if "stepfun" in normalized_model or normalized_model.startswith("step/"):
        return "stepfun"
    if normalized_model.startswith("anthropic/"):
        return "anthropic"
    if normalized_model.startswith("gemini/"):
        return "gemini"
    if normalized_model.startswith("deepseek/"):
        return "deepseek"
    if "/" in normalized_model:
        return normalized_model.split("/", 1)[0]
    return normalized_provider or "unknown"


def _is_glm_model(normalized_model: str) -> bool:
    if not normalized_model:
        return False
    return (
        normalized_model.startswith("glm")
        or normalized_model.startswith("zhipu/")
        or normalized_model.startswith("bigmodel/")
        or "/glm" in normalized_model
    )


def _is_native_openai_model(model: str) -> bool:
    normalized = (model or "").strip().lower()
    if normalized.startswith("openai/"):
        normalized = normalized.split("/", 1)[1]
    if not normalized or "/" in normalized:
        return False
    return normalized.startswith(
        (
            "gpt-",
            "gpt4",
            "gpt5",
            "chatgpt-",
            "o1",
            "o3",
            "o4",
            "text-",
            "davinci",
            "babbage",
            "curie",
            "ada",
        )
    )


def _first_int(mapping: Mapping[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = mapping.get(key)
        parsed = _as_int(value)
        if parsed is not None:
            return parsed
    return None


def _nested_int(mapping: Mapping[str, Any], path: Iterable[str]) -> Optional[int]:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return _as_int(current)


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _eligible_input_tokens(
    prompt_tokens: Optional[int],
    provider_min_cache_tokens: Optional[int],
    capability: str,
) -> Optional[int]:
    if capability != "supported" or prompt_tokens is None:
        return None
    if provider_min_cache_tokens is not None and prompt_tokens < provider_min_cache_tokens:
        return None
    return prompt_tokens


def _cache_eligibility(
    prompt_tokens: Optional[int],
    provider_min_cache_tokens: Optional[int],
    capability: str,
) -> str:
    if capability != "supported":
        return "unknown"
    if prompt_tokens is None:
        return "unknown"
    if provider_min_cache_tokens is not None and prompt_tokens < provider_min_cache_tokens:
        return "below_threshold"
    return "eligible"


def _cache_observation(
    *,
    has_usage: bool,
    cache_field_observed: bool,
    cache_read: Optional[int],
    cache_write: Optional[int],
    cache_miss: Optional[int],
    eligible_tokens: Optional[int],
) -> str:
    if not has_usage:
        return "no_usage"
    if not cache_field_observed:
        return "unknown"
    read = cache_read or 0
    write = cache_write or 0
    miss = cache_miss or 0
    if read > 0 and write > 0:
        return "read_and_write"
    if write > 0:
        return "write_only"
    if read > 0:
        hit_ratio = _ratio(read, eligible_tokens)
        return "full_hit" if hit_ratio is not None and hit_ratio >= 0.9 else "partial_hit"
    if eligible_tokens is None:
        return "unknown"
    if miss > 0 or cache_field_observed:
        return "zero_hit"
    return "unknown"


def _has_invalid_cache_usage(
    *,
    prompt_tokens: Optional[int],
    cache_read: Optional[int],
    cache_write: Optional[int],
    cache_miss: Optional[int],
    capability: str,
) -> bool:
    if capability != "supported":
        return False
    values = (prompt_tokens, cache_read, cache_write, cache_miss)
    if any(value is not None and value < 0 for value in values):
        return True
    if prompt_tokens is None:
        return False
    read = cache_read or 0
    write = cache_write or 0
    miss = cache_miss or 0
    if read > prompt_tokens or write > prompt_tokens or miss > prompt_tokens:
        return True
    if read + write > prompt_tokens:
        return True
    if cache_miss is not None and read + miss > prompt_tokens:
        return True
    return False


def _ratio(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _reset_usage_hmac_secret_cache_for_tests() -> None:
    global _HMAC_SECRET_CACHE
    _HMAC_SECRET_CACHE = None
