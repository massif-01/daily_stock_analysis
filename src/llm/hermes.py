# -*- coding: utf-8 -*-
"""Hermes local HTTP channel helpers."""

from __future__ import annotations

import copy
import ipaddress
import socket
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

HERMES_CHANNEL_ID = "hermes"
HERMES_DEFAULT_BASE_URL = "http://127.0.0.1:8642/v1"
HERMES_DEFAULT_MODEL = "hermes-agent"
HERMES_ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
HERMES_MODEL_INFO_CHANNEL_KEY = "dsa_channel"
HERMES_PROTOCOL = "openai"
HERMES_ALLOWED_PROTOCOL_ALIASES = frozenset(
    {
        "",
        "openai",
        "openai-compatible",
        "openai_compatible",
        "openai-compat",
        "openai_compat",
    }
)
HERMES_PROVIDER_LIKE_PREFIXES = frozenset(
    {
        "anthropic",
        "azure",
        "bedrock",
        "cerebras",
        "cohere",
        "command-r",
        "deepseek",
        "fireworks_ai",
        "gemini",
        "groq",
        "huggingface",
        "minimax",
        "ollama",
        "openrouter",
        "palm",
        "replicate",
        "sagemaker",
        "text-completion-openai",
        "together_ai",
        "vertex_ai",
    }
)


def is_hermes_channel_name(name: str) -> bool:
    return (name or "").strip().lower() == HERMES_CHANNEL_ID


def _issue(code: str, message: str, expected: str, actual: str) -> Dict[str, str]:
    return {
        "code": code,
        "message": message,
        "expected": expected,
        "actual": actual,
    }


def normalize_hermes_protocol(value: str) -> tuple[str, Optional[Dict[str, str]]]:
    """Normalize the sealed Hermes transport protocol."""
    raw_value = (value or "").strip()
    normalized = raw_value.lower().replace("_", "-")
    if normalized in {"", "openai", "openai-compatible", "openai-compat"}:
        return HERMES_PROTOCOL, None
    return "", _issue(
        "invalid_hermes_protocol",
        "Hermes transport is fixed to OpenAI-compatible protocol",
        "openai",
        raw_value,
    )


def normalize_hermes_model(model: str) -> tuple[str, Optional[Dict[str, str]]]:
    """Normalize a Hermes wire model id into the OpenAI-compatible LiteLLM route."""
    raw_model = (model or "").strip()
    if not raw_model:
        return "", _issue(
            "missing_models",
            "Hermes channel requires at least one model",
            HERMES_DEFAULT_MODEL,
            "",
        )
    if "/" not in raw_model:
        return f"{HERMES_PROTOCOL}/{raw_model}", None

    prefix, remainder = raw_model.split("/", 1)
    normalized_prefix = prefix.strip().lower()
    if normalized_prefix == HERMES_PROTOCOL and remainder.strip():
        return f"{HERMES_PROTOCOL}/{remainder.strip()}", None
    if normalized_prefix in HERMES_PROVIDER_LIKE_PREFIXES:
        return "", _issue(
            "invalid_hermes_model",
            (
                "Hermes transport is fixed to OpenAI-compatible. If this is the "
                "Hermes runtime wire model id, write it as openai/<wire-model-id>, "
                f"e.g. openai/{raw_model}."
            ),
            f"openai/{raw_model}",
            raw_model,
        )
    return f"{HERMES_PROTOCOL}/{raw_model}", None


def normalize_hermes_models(models: List[str]) -> tuple[List[str], List[Dict[str, str]]]:
    """Normalize Hermes model ids while keeping provider and wire id separate."""
    raw_models = [str(model).strip() for model in models or [] if str(model).strip()]
    if not raw_models:
        raw_models = [HERMES_DEFAULT_MODEL]

    normalized_models: List[str] = []
    issues: List[Dict[str, str]] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        normalized_model, issue = normalize_hermes_model(raw_model)
        if issue:
            issues.append(issue)
            continue
        if normalized_model and normalized_model not in seen:
            seen.add(normalized_model)
            normalized_models.append(normalized_model)
    return normalized_models, issues


def hermes_protocol_issue_for_field(protocol: str, field_key: str) -> Optional[Dict[str, Any]]:
    _, issue = normalize_hermes_protocol(protocol)
    if not issue:
        return None
    return {
        "key": field_key,
        "code": issue["code"],
        "message": issue["message"],
        "severity": "error",
        "expected": issue["expected"],
        "actual": issue["actual"],
    }


def hermes_model_issues_for_field(models: List[str], field_key: str) -> List[Dict[str, Any]]:
    _, issues = normalize_hermes_models(models)
    return [
        {
            "key": field_key,
            "code": issue["code"],
            "message": issue["message"],
            "severity": "error",
            "expected": issue["expected"],
            "actual": issue["actual"],
        }
        for issue in issues
    ]


def _canonical_ipv4_numeric_host(host: str) -> Optional[str]:
    candidate = (host or "").lower()
    if not candidate or ":" in candidate:
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(candidate))
    except (OSError, ValueError):
        return None


def _is_noncanonical_ipv4_numeric_host(host: str) -> bool:
    canonical = _canonical_ipv4_numeric_host(host)
    return canonical is not None and host.lower() != canonical


def normalize_hostname_for_security(host: str) -> Optional[str]:
    """Return a normalized ASCII host for URL safety checks."""
    candidate = (host or "").strip().lower().rstrip(".")
    if not candidate:
        return None
    if ":" in candidate:
        return candidate
    try:
        normalized = unicodedata.normalize("NFKC", candidate)
        ascii_host = normalized.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return None
    return ascii_host or None


def validate_hermes_base_url(value: str) -> Optional[Dict[str, str]]:
    """Return a structured issue when a Hermes base URL violates P0 rules."""
    raw_value = (value or "").strip()
    if not raw_value:
        return {
            "code": "missing_base_url",
            "message": "Hermes channel requires a loopback base URL",
            "expected": "http://127.0.0.1:8642/v1",
            "actual": value or "",
        }
    if any(char == "\\" or char.isspace() or ord(char) < 32 or ord(char) == 127 for char in raw_value):
        return {
            "code": "invalid_url",
            "message": "Hermes channel base URL must be a valid absolute URL",
            "expected": "http(s)://127.0.0.1 or http(s)://localhost",
            "actual": value,
        }

    try:
        parsed = urlparse(raw_value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        return {
            "code": "invalid_url",
            "message": "Hermes channel base URL must be a valid absolute URL",
            "expected": "http(s)://host",
            "actual": value,
        }

    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not host:
        return {
            "code": "invalid_url",
            "message": "Hermes channel base URL must be a valid absolute URL",
            "expected": "http(s)://host",
            "actual": value,
        }
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        return {
            "code": "invalid_url",
            "message": "Hermes channel base URL must not include username or password",
            "expected": "URL without credentials",
            "actual": value,
        }
    if _is_noncanonical_ipv4_numeric_host(host):
        return {
            "code": "invalid_url",
            "message": "Hermes channel base URL host must use canonical IP or hostname syntax",
            "expected": "127.0.0.1, localhost, or ::1",
            "actual": value,
        }

    normalized_host = normalize_hostname_for_security(host)
    if not normalized_host:
        return {
            "code": "invalid_url",
            "message": "Hermes channel base URL host is invalid",
            "expected": "127.0.0.1, localhost, or ::1",
            "actual": value,
        }

    if normalized_host == "localhost":
        return None
    try:
        ip_addr = ipaddress.ip_address(normalized_host)
    except ValueError:
        return {
            "code": "hermes_loopback_only",
            "message": "Hermes channel only allows loopback client URLs in Phase 3 P0",
            "expected": "127.0.0.1, localhost, or ::1",
            "actual": value,
        }

    if str(ip_addr) in HERMES_ALLOWED_LOOPBACK_HOSTS and ip_addr.is_loopback:
        return None
    return {
        "code": "hermes_loopback_only",
        "message": "Hermes channel only allows loopback client URLs in Phase 3 P0",
        "expected": "127.0.0.1, localhost, or ::1",
        "actual": value,
    }


def hermes_issue_for_field(base_url: str, field_key: str) -> Optional[Dict[str, Any]]:
    issue = validate_hermes_base_url(base_url)
    if not issue:
        return None
    return {
        "key": field_key,
        "code": issue["code"],
        "message": issue["message"],
        "severity": "error",
        "expected": issue["expected"],
        "actual": issue["actual"],
    }


def get_hermes_channel_models(config: Any) -> set[str]:
    models: set[str] = set()
    for channel in getattr(config, "llm_channels", []) or []:
        if not is_hermes_channel_name(str(channel.get("name") or "")):
            continue
        models.update(str(model).strip() for model in channel.get("models") or [] if str(model).strip())
    return models


def get_declared_hermes_models(config: Any) -> set[str]:
    models = get_hermes_channel_models(config)
    models.update(
        str(model).strip()
        for model in getattr(config, "llm_declared_hermes_models", []) or []
        if str(model).strip()
    )
    return models


def _model_candidates(model: str) -> set[str]:
    candidate = (model or "").strip()
    if not candidate:
        return set()
    candidates = {candidate}
    if "/" not in candidate:
        candidates.add(f"openai/{candidate}")
    return candidates


def _deployment_model_name(entry: Dict[str, Any]) -> str:
    model_name = str(entry.get("model_name") or "").strip()
    if model_name:
        return model_name
    params = entry.get("litellm_params", {}) or {}
    return str(params.get("model") or "").strip()


def _deployment_matches_model(entry: Dict[str, Any], model: str) -> bool:
    candidates = _model_candidates(model)
    if not candidates:
        return False
    deployment_model = _deployment_model_name(entry)
    return bool(deployment_model and deployment_model in candidates)


def is_hermes_deployment(entry: Any) -> bool:
    """Return whether a LiteLLM model_list deployment is a Hermes channel entry."""
    if not isinstance(entry, dict):
        return False
    model_info = entry.get("model_info") or {}
    if not isinstance(model_info, dict):
        return False
    return (
        str(model_info.get(HERMES_MODEL_INFO_CHANNEL_KEY) or "").strip().lower()
        == HERMES_CHANNEL_ID
    )


def filter_agent_model_list(model_list: Any) -> List[Dict[str, Any]]:
    """Return an Agent-safe model_list with Hermes deployments removed."""
    if not model_list:
        return []
    filtered: List[Dict[str, Any]] = []
    for entry in model_list:
        if not isinstance(entry, dict) or is_hermes_deployment(entry):
            continue
        filtered.append(copy.deepcopy(entry))
    return filtered


def is_hermes_only_route(
    config: Any,
    model: str,
    agent_model_list: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Return whether a route has only Hermes backing and cannot serve Agent tools."""
    candidates = _model_candidates(model)
    if not candidates:
        return False

    safe_model_list = (
        agent_model_list
        if agent_model_list is not None
        else filter_agent_model_list(getattr(config, "llm_model_list", []) or [])
    )
    for entry in safe_model_list or []:
        if isinstance(entry, dict) and _deployment_matches_model(entry, model):
            return False

    matching_deployments = [
        entry
        for entry in getattr(config, "llm_model_list", []) or []
        if isinstance(entry, dict) and _deployment_matches_model(entry, model)
    ]
    if matching_deployments:
        return all(is_hermes_deployment(entry) for entry in matching_deployments)

    declared_models = get_declared_hermes_models(config)
    return any(candidate in declared_models for candidate in candidates)
