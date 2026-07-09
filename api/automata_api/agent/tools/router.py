from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from automata_api.agent.backends.base import Backend
from automata_api.agent.tools._core import ToolResult
from automata_api.agent.tools.model import (
    ToolDescriptor,
    ToolDiscoveryContext,
    ToolExposure,
    ToolProvider,
)
from automata_api.agent.tools.providers import BackendToolProvider
from automata_api.agent.tools.registry import ToolRegistry
from automata_api.agent.tools.tool_search import (
    TOOL_SEARCH_NAME,
    run_tool_search,
    tool_search_spec,
)


class ToolRouter:
    def __init__(self, descriptors: Iterable[ToolDescriptor]) -> None:
        self._descriptors = tuple(descriptors)
        self._descriptors_by_name = build_descriptor_index(self._descriptors)
        self._activated_deferred: set[str] = set()
        self._registry = ToolRegistry(
            descriptor.executor for descriptor in self._descriptors
        )

    @classmethod
    def from_backend(
        cls,
        backend: Backend,
        *,
        session_id: str | None = None,
        workspace: str | None = None,
        mode: str = "act",
        providers: Iterable[ToolProvider] | None = None,
    ) -> "ToolRouter":
        provider_list = tuple(providers or (BackendToolProvider(),))
        context = ToolDiscoveryContext(
            session_id=session_id,
            workspace=workspace or backend.workspace_label,
            backend=backend,
            mode=mode,
        )
        return cls.from_providers(context, provider_list)

    @classmethod
    def from_providers(
        cls,
        context: ToolDiscoveryContext,
        providers: Iterable[ToolProvider],
    ) -> "ToolRouter":
        descriptors: list[ToolDescriptor] = []
        for provider in providers:
            descriptors.extend(provider.discover(context))
        return cls(descriptors)

    @classmethod
    def default_for_workspace(cls, workspace: str) -> "ToolRouter":
        from automata_api.agent.backends.local import LocalBackend

        backend = LocalBackend(workspace)
        return cls.from_backend(backend, workspace=workspace)

    def model_visible_specs(self, *, mode: str = "act") -> list[dict[str, Any]]:
        specs = [
            descriptor.spec
            for descriptor in self._descriptors
            if self._is_model_visible(descriptor, mode=mode)
        ]
        if self._search_candidates(mode=mode):
            specs.append(tool_search_spec())
        return specs

    def allowed_names(self, *, mode: str = "act") -> set[str]:
        names = {
            descriptor.name
            for descriptor in self._descriptors
            if self._is_model_visible(descriptor, mode=mode)
        }
        if self._search_candidates(mode=mode):
            names.add(TOOL_SEARCH_NAME)
        return names

    async def dispatch(
        self,
        name: str,
        raw_arguments: str | dict[str, Any] | None,
        *,
        mode: str = "act",
    ) -> ToolResult:
        if name == TOOL_SEARCH_NAME:
            return run_tool_search(
                raw_arguments,
                candidates=self._search_candidates(mode=mode),
                mode=mode,
                activate=self.activate_deferred,
            )

        descriptor = self._descriptors_by_name.get(name)
        if descriptor is None:
            if mode == "plan":
                return blocked_by_plan_mode(name, raw_arguments, mode, self.allowed_names(mode=mode))
            return await self._registry.dispatch(name, raw_arguments)

        if mode == "plan" and not descriptor.read_only:
            return blocked_by_plan_mode(name, raw_arguments, mode, self.allowed_names(mode=mode))

        if descriptor.exposure == ToolExposure.HIDDEN:
            return unavailable_tool_result(name, raw_arguments, "tool_not_available")

        if (
            descriptor.exposure == ToolExposure.DEFERRED
            and descriptor.name not in self._activated_deferred
        ):
            return unavailable_tool_result(
                name,
                raw_arguments,
                "tool_not_loaded",
                hint=f"Use {TOOL_SEARCH_NAME} before calling deferred tool: {name}",
            )

        return await self._registry.dispatch(name, raw_arguments)

    def activate_deferred(self, names: Iterable[str]) -> None:
        for name in names:
            descriptor = self._descriptors_by_name.get(name)
            if descriptor is not None and descriptor.exposure == ToolExposure.DEFERRED:
                self._activated_deferred.add(name)

    def registered_names(self) -> set[str]:
        return set(self._descriptors_by_name)

    def _is_model_visible(self, descriptor: ToolDescriptor, *, mode: str) -> bool:
        if mode == "plan" and not descriptor.read_only:
            return False
        if descriptor.exposure == ToolExposure.DIRECT:
            return True
        return (
            descriptor.exposure == ToolExposure.DEFERRED
            and descriptor.name in self._activated_deferred
        )

    def _search_candidates(self, *, mode: str) -> list[ToolDescriptor]:
        return [
            descriptor
            for descriptor in self._descriptors
            if descriptor.exposure == ToolExposure.DEFERRED
            and descriptor.name not in self._activated_deferred
            and (mode != "plan" or descriptor.read_only)
        ]


def build_descriptor_index(
    descriptors: Iterable[ToolDescriptor],
) -> dict[str, ToolDescriptor]:
    index: dict[str, ToolDescriptor] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor.name, str) or not descriptor.name.strip():
            raise ValueError(f"Registered tool name must be a non-empty string: {descriptor!r}")
        if descriptor.name in index:
            raise ValueError(f"Duplicate tool registered: {descriptor.name}")
        index[descriptor.name] = descriptor
    return index


def blocked_by_plan_mode(
    name: str,
    raw_arguments: str | dict[str, Any] | None,
    mode: str,
    allowed_tool_names: set[str],
) -> ToolResult:
    arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
    return ToolResult(
        name=name,
        arguments=arguments,
        content=json.dumps(
            {
                "simulated": False,
                "ok": False,
                "tool": name,
                "mode": mode,
                "error": "blocked_by_plan_mode",
                "allowed_tools": sorted(allowed_tool_names),
            },
            ensure_ascii=True,
        ),
        success=False,
    )


def unavailable_tool_result(
    name: str,
    raw_arguments: str | dict[str, Any] | None,
    error: str,
    *,
    hint: str | None = None,
) -> ToolResult:
    arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
    payload = {
        "simulated": False,
        "ok": False,
        "tool": name,
        "error": error,
    }
    if hint:
        payload["hint"] = hint
    return ToolResult(
        name=name,
        arguments=arguments,
        content=json.dumps(payload, ensure_ascii=True),
        success=False,
    )
