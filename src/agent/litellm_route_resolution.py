# -*- coding: utf-8 -*-
"""Shared LiteLLM route resolution for Agent runtime and display."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.config import (
    _uses_direct_env_provider,
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


def _deployment_route_model(entry: Dict[str, Any]) -> str:
    return str(entry.get("model_name") or "").strip()


def _deployment_wire_model(entry: Dict[str, Any]) -> str:
    params = entry.get("litellm_params", {}) or {}
    return str(params.get("model") or "").strip()


def _is_legacy_placeholder_model(model_name: str) -> bool:
    return str(model_name or "").startswith("__legacy_")


def _deployment_matches_model(entry: Dict[str, Any], model_name: str) -> bool:
    candidate = str(model_name or "").strip()
    if not candidate:
        return False
    route_model = _deployment_route_model(entry)
    wire_model = _deployment_wire_model(entry)
    if _is_legacy_placeholder_model(route_model):
        return False
    return candidate in {route_model, wire_model}


def _matching_router_deployments(
    agent_model_list: List[Dict[str, Any]],
    model_name: str,
) -> List[tuple[int, Dict[str, Any]]]:
    return [
        (index, entry)
        for index, entry in enumerate(agent_model_list)
        if _deployment_matches_model(entry, model_name)
    ]


def _model_has_runtime_route(
    config: Any,
    agent_model_list: List[Dict[str, Any]],
    model_name: str,
) -> bool:
    if _matching_router_deployments(agent_model_list, model_name):
        return True
    if _uses_direct_env_provider(model_name):
        return True
    provider = _get_model_provider(model_name)
    if provider in _MANAGED_LEGACY_PROVIDERS:
        if get_api_keys_for_model(model_name, config):
            return True
        return any(
            _PLACEHOLDER_TO_PROVIDER.get(entry.get("model_name")) == provider
            for entry in getattr(config, "llm_model_list", []) or []
        )
    return False


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


def _build_router_deployment(
    source: str,
    resolution: AgentLiteLLMRouteResolution,
    *,
    model_name: str,
    entry: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    route_model = _deployment_route_model(entry)
    wire_model = _deployment_wire_model(entry)
    display_model = route_model or wire_model or model_name
    params = entry.get("litellm_params", {}) or {}
    api_base = params.get("api_base")
    fallback_models = set(resolution.models_to_try[1:])
    return {
        "deployment_id": f"{source}:{index}",
        "model": display_model,
        "provider": _get_model_provider(wire_model or display_model),
        "source": source,
        "api_base": str(api_base).strip() if api_base else None,
        "deployment_name": route_model or None,
        "is_primary": model_name == resolution.primary_model or display_model == resolution.primary_model,
        "is_fallback": model_name in fallback_models or display_model in fallback_models,
    }


def _build_runtime_deployments(
    config: Any,
    resolution: AgentLiteLLMRouteResolution,
) -> List[Dict[str, Any]]:
    source = _get_models_source(config)
    deployments: List[Dict[str, Any]] = []
    seen_models: set[str] = set()

    for model_name in resolution.models_to_try:
        if not model_name or model_name in seen_models:
            continue
        seen_models.add(model_name)
        matches = _matching_router_deployments(resolution.agent_model_list, model_name)
        if matches:
            for index, entry in matches:
                deployments.append(
                    _build_router_deployment(
                        source,
                        resolution,
                        model_name=model_name,
                        entry=entry,
                        index=index,
                    )
                )
            continue
        deployments.extend(_build_direct_or_legacy_deployments(config, resolution, model_name))

    return deployments


def _build_direct_or_legacy_deployments(
    config: Any,
    resolution: AgentLiteLLMRouteResolution,
    model_name: str,
) -> List[Dict[str, Any]]:
    placeholder_counts = {provider: 0 for provider in _PLACEHOLDER_TO_PROVIDER.values()}
    for entry in getattr(config, "llm_model_list", []) or []:
        provider = _PLACEHOLDER_TO_PROVIDER.get(entry.get("model_name"))
        if provider:
            placeholder_counts[provider] += 1

    deployments: List[Dict[str, Any]] = []
    fallback_set = set(resolution.models_to_try[1:])
    provider = _get_model_provider(model_name)
    api_base = getattr(config, "openai_base_url", None) if provider == "openai" else None
    if provider == "openai" and not api_base:
        api_base = extra_litellm_params(model_name, config).get("api_base")

    if _uses_direct_env_provider(model_name):
        return [
            {
                "deployment_id": f"direct_env:{provider}:0:{model_name}",
                "model": model_name,
                "provider": provider,
                "source": "direct_env",
                "api_base": api_base,
                "deployment_name": f"direct_env_{provider}_1",
                "is_primary": model_name == resolution.primary_model,
                "is_fallback": model_name in fallback_set,
            }
        ]

    deployment_count = placeholder_counts.get(provider, 0)
    if deployment_count <= 0:
        if provider in _MANAGED_LEGACY_PROVIDERS and not get_api_keys_for_model(model_name, config):
            return []
        deployment_count = 1

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

    raw_models_to_try = get_effective_agent_models_to_try(config)
    hermes_models = {
        model
        for model in raw_models_to_try
        if is_hermes_only_route(config, model, resolution.agent_model_list)
    }
    resolution.models_to_try = []
    for index, model in enumerate(raw_models_to_try):
        if model in hermes_models:
            continue
        if index > 0 and not _model_has_runtime_route(config, resolution.agent_model_list, model):
            continue
        resolution.models_to_try.append(model)
    if resolution.primary_model not in resolution.models_to_try:
        resolution.models_to_try.insert(0, resolution.primary_model)

    deployments = _build_runtime_deployments(config, resolution)
    resolution.deployments_for_display = _sort_deployments(deployments)
    return resolution
