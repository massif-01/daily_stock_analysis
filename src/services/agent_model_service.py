# -*- coding: utf-8 -*-
"""Helpers for exposing configured Agent model deployments."""

from __future__ import annotations

from typing import Any, Dict, List

from src.agent.litellm_route_resolution import resolve_agent_litellm_routes


def list_agent_model_deployments(config) -> List[Dict[str, Any]]:
    """Return configured Agent model deployments without exposing secrets."""
    resolution = resolve_agent_litellm_routes(config)
    if resolution.backend_error is not None:
        return []
    return resolution.deployments_for_display
