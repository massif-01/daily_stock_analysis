# -*- coding: utf-8 -*-
"""Shared LiteLLM route resolution for Agent runtime and display."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.config import (
    extra_litellm_params,
    get_api_keys_for_model,
    get_effective_agent_models_to_try,
    get_effective_agent_primary_model,
)
from src.llm.backend_registry import (
    AUTO_AGENT_BACKEND_ID,
    CODEX_CLI_BACKEND_ID,
    LITELLM_BACKEND_ID,
    resolve_agent_generation_backend_id,
)
from src.llm.generation_backend import GenerationError, GenerationErrorCode
from src.llm.hermes import filter_agent_model_list, is_hermes_only_route


_PLACEHOLDER_TO_PROVIDER = {
    "__legacy_gemini__": "gemini",
    "__legacy_anthropic__": "anthropic",
    "__legacy_openai__": "openai",
    "__legacy_deepseek__": "deepseek",
}
_MANAGED_LEGACY_PROVIDERS = set(_PLACEHOLDER_TO_PROVIDER.values())


@dataclass
class AgentLiteLLMRouteResolution:
    primary_model: str = ""
    models_to_try: List[str] = field(default_factory=list)
    agent_model_list: List[Dict[str, Any]] = field(default_factory=list)
    deployments_for_display: List[Dict[str, Any]] = field(default_factory=list)
    has_channel_config: bool = False
    backend_error: Optional[GenerationError] = None
    unsupported_reason: Optional[str] = None
    generation_backend_id: str = ""


def _get_models_source(config: Any) -> str:
    source = getattr(config, "llm_models_source", "")
    if source in {"litellm_config", "llm_channels", "legacy_env"}:
        return source
    return "legacy_env"


def _get_model_provider(model_name: str) -> str:
    if not model_name:
        return "unknown"
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    return "openai"


def _has_channel_config(agent_model_list: List[Dict[str, Any]]) -> bool:
    return bool(agent_model_list) and not all(
        str(entry.get("model_name", "")).startswith("__legacy_")
        for entry in agent_model_list
    )


def _unsupported_backend_error(backend_id: str) -> GenerationError:
    return GenerationError(
        error_code=GenerationErrorCode.UNSUPPORTED_TOOL_CALLING,
        stage="generation",
        retryable=False,
        fallbackable=False,
        backend=backend_id,
        provider=backend_id,
        details={
            "field": "AGENT_GENERATION_BACKEND",
            "requested_backend": backend_id,
            "supported_tool_backend": LITELLM_BACKEND_ID,
        },
    )


def _codex_auto_backend_error() -> GenerationError:
    return GenerationError(
        error_code=GenerationErrorCode.UNSUPPORTED_TOOL_CALLING,
        stage="generation",
        retryable=False,
        fallbackable=False,
        backend=CODEX_CLI_BACKEND_ID,
        provider=CODEX_CLI_BACKEND_ID,
        details={
            "field": "AGENT_GENERATION_BACKEND",
            "requested_backend": AUTO_AGENT_BACKEND_ID,
            "generation_backend": CODEX_CLI_BACKEND_ID,
            "supported_tool_backend": LITELLM_BACKEND_ID,
            "reason": "litellm_agent_backend_unavailable",
        },
    )


def _hermes_backend_error(model: str) -> GenerationError:
    return GenerationError(
        error_code=GenerationErrorCode.UNSUPPORTED_TOOL_CALLING,
        stage="generation",
        retryable=False,
        fallbackable=False,
        backend="hermes",
        provider="hermes",
        details={
            "field": "AGENT_LITELLM_MODEL",
            "requested_model": model,
            "reason": "hermes_generation_only_shape_probe",
            "supported_tool_backend": LITELLM_BACKEND_ID,
        },
    )


def _build_non_legacy_deployments(
    config: Any,
    resolution: AgentLiteLLMRouteResolution,
) -> List[Dict[str, Any]]:
    source = _get_models_source(config)
    fallback_models = set(resolution.models_to_try[1:])
    deployments: List[Dict[str, Any]] = []

    for index, entry in enumerate(resolution.agent_model_list):
        params = entry.get("litellm_params", {}) or {}
        route_model = str(entry.get("model_name") or "").strip()
        wire_model = str(params.get("model") or "").strip()
        display_model = route_model or wire_model
        if not display_model or display_model.startswith("__legacy_"):
            continue

        api_base = params.get("api_base")
        deployments.append(
            {
                "deployment_id": f"{source}:{index}",
                "model": display_model,
                "provider": _get_model_provider(wire_model or display_model),
                "source": source,
                "api_base": str(api_base).strip() if api_base else None,
                "deployment_name": route_model or None,
                "is_primary": display_model == resolution.primary_model,
                "is_fallback": display_model in fallback_models,
            }
        )

    return deployments


def _build_legacy_deployments(
    config: Any,
    resolution: AgentLiteLLMRouteResolution,
) -> List[Dict[str, Any]]:
    if not resolution.models_to_try:
        return []

    placeholder_counts = {provider: 0 for provider in _PLACEHOLDER_TO_PROVIDER.values()}
    for entry in getattr(config, "llm_model_list", []) or []:
        provider = _PLACEHOLDER_TO_PROVIDER.get(entry.get("model_name"))
        if provider:
            placeholder_counts[provider] += 1

    deployments: List[Dict[str, Any]] = []
    seen_models = set()
    fallback_set = set(resolution.models_to_try[1:])
    for model_name in resolution.models_to_try:
        if model_name in seen_models:
            continue
        seen_models.add(model_name)

        provider = _get_model_provider(model_name)
        deployment_count = placeholder_counts.get(provider, 0)
        if deployment_count <= 0:
            if provider in _MANAGED_LEGACY_PROVIDERS and not get_api_keys_for_model(model_name, config):
                continue
            deployment_count = 1

        api_base = getattr(config, "openai_base_url", None) if provider == "openai" else None
        if provider == "openai" and not api_base:
            api_base = extra_litellm_params(model_name, config).get("api_base")
        deployment_indexes = range(deployment_count) if model_name == resolution.primary_model else range(1)

        for index in deployment_indexes:
            deployments.append(
                {
                    "deployment_id": f"legacy:{provider}:{index}:{model_name}",
                    "model": model_name,
                    "provider": provider,
                    "source": "legacy_env",
                    "api_base": api_base,
                    "deployment_name": f"legacy_{provider}_{index + 1}",
                    "is_primary": model_name == resolution.primary_model,
                    "is_fallback": model_name in fallback_set,
                }
            )

    return deployments


def _sort_deployments(deployments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        deployments,
        key=lambda item: (
            not item["is_primary"],
            not item["is_fallback"],
            item["source"],
            item["model"],
            item["api_base"] or "",
            item["deployment_name"] or "",
            item["deployment_id"],
        ),
    )


def resolve_agent_litellm_routes(config: Any) -> AgentLiteLLMRouteResolution:
    """Resolve the Agent LiteLLM route once for runtime and API display."""
    resolution = AgentLiteLLMRouteResolution()
    resolution.agent_model_list = filter_agent_model_list(getattr(config, "llm_model_list", []) or [])
    resolution.has_channel_config = _has_channel_config(resolution.agent_model_list)

    try:
        resolution.generation_backend_id = resolve_agent_generation_backend_id(config)
    except GenerationError as exc:
        resolution.backend_error = exc
        resolution.unsupported_reason = exc.details.get("reason") if isinstance(exc.details, dict) else None
        return resolution

    if resolution.generation_backend_id != LITELLM_BACKEND_ID:
        resolution.backend_error = _unsupported_backend_error(resolution.generation_backend_id)
        resolution.unsupported_reason = "unsupported_tool_calling"
        return resolution

    resolution.primary_model = get_effective_agent_primary_model(config)
    if not resolution.primary_model:
        generation_backend = str(
            getattr(config, "generation_backend", LITELLM_BACKEND_ID) or LITELLM_BACKEND_ID
        ).strip().lower()
        agent_backend = str(
            getattr(config, "agent_generation_backend", AUTO_AGENT_BACKEND_ID)
            or AUTO_AGENT_BACKEND_ID
        ).strip().lower()
        if generation_backend == CODEX_CLI_BACKEND_ID and agent_backend == AUTO_AGENT_BACKEND_ID:
            resolution.backend_error = _codex_auto_backend_error()
            resolution.unsupported_reason = "litellm_agent_backend_unavailable"
        return resolution

    if is_hermes_only_route(config, resolution.primary_model, resolution.agent_model_list):
        resolution.backend_error = _hermes_backend_error(resolution.primary_model)
        resolution.unsupported_reason = "hermes_generation_only_shape_probe"
        return resolution

    hermes_models = {
        model
        for model in get_effective_agent_models_to_try(config)
        if is_hermes_only_route(config, model, resolution.agent_model_list)
    }
    resolution.models_to_try = [
        model
        for model in get_effective_agent_models_to_try(config)
        if model not in hermes_models
    ]
    if resolution.primary_model not in resolution.models_to_try:
        resolution.models_to_try.insert(0, resolution.primary_model)

    deployments = _build_non_legacy_deployments(config, resolution)
    if not deployments:
        deployments = _build_legacy_deployments(config, resolution)
    resolution.deployments_for_display = _sort_deployments(deployments)
    return resolution
