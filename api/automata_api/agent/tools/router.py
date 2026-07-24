from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

from automata_api.agent.backends.base import Backend
from automata_api.agent.tools._core import ToolResult
from automata_api.agent.tools.model import (
    ToolDescriptor,
    ToolDiscoveryContext,
    ToolExposure,
    AsyncToolProvider,
    ToolProvider,
)
from automata_api.agent.tools.providers import BackendToolProvider
from automata_api.agent.tools.registry import ToolRegistry
from automata_api.agent.tools.tool_search import (
    TOOL_SEARCH_NAME,
    run_tool_search,
    tool_search_spec,
)


logger = logging.getLogger(__name__)
DEFAULT_MAX_MODEL_TOOLS = 128


class ToolRouter:
    def __init__(
        self,
        descriptors: Iterable[ToolDescriptor],
        *,
        max_model_tools: int = DEFAULT_MAX_MODEL_TOOLS,
    ) -> None:
        if max_model_tools <= 0:
            raise ValueError("max_model_tools must be greater than zero")
        self._max_model_tools = max_model_tools
        self._activated_deferred: dict[str, None] = {}
        self._install_descriptors(tuple(descriptors))

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
        visible = self._visible_descriptors(mode=mode)
        specs = [descriptor.spec for descriptor in visible]
        if (
            len(specs) < self._max_model_tools
            and self._search_candidates(mode=mode)
        ):
            specs.append(tool_search_spec())
        return specs

    def allowed_names(self, *, mode: str = "act") -> set[str]:
        names = {
            descriptor.name for descriptor in self._visible_descriptors(mode=mode)
        }
        if (
            len(names) < self._max_model_tools
            and self._search_candidates(mode=mode)
        ):
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
                activate=lambda names: self.activate_deferred(names, mode=mode),
            )

        descriptor = self._descriptors_by_name.get(name)
        if descriptor is None:
            if mode == "plan":
                return blocked_by_plan_mode(name, raw_arguments, mode, self.allowed_names(mode=mode))
            return await self._registry.dispatch(name, raw_arguments, mode=mode)

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

        return await self._registry.dispatch(name, raw_arguments, mode=mode)

    def execution_descriptor(
        self, name: str, *, mode: str = "act"
    ) -> ToolDescriptor | None:
        if name == TOOL_SEARCH_NAME:
            return None
        descriptor = self._descriptors_by_name.get(name)
        if descriptor is None or not self._is_model_visible(descriptor, mode=mode):
            return None
        return descriptor

    async def dispatch_authorized(
        self,
        name: str,
        raw_arguments: str | dict[str, Any] | None,
        *,
        mode: str = "act",
    ) -> ToolResult:
        if name == TOOL_SEARCH_NAME:
            return await self.dispatch(name, raw_arguments, mode=mode)
        descriptor = self._descriptors_by_name.get(name)
        if descriptor is None:
            return await self.dispatch(name, raw_arguments, mode=mode)
        if mode == "plan" and not descriptor.read_only:
            return blocked_by_plan_mode(
                name, raw_arguments, mode, self.allowed_names(mode=mode)
            )
        if not self._is_model_visible(descriptor, mode=mode):
            return await self.dispatch(name, raw_arguments, mode=mode)
        return await self._registry.run_authorized(
            name, raw_arguments, mode=mode
        )

    def activate_deferred(
        self, names: Iterable[str], *, mode: str = "act"
    ) -> list[str]:
        activated: list[str] = []
        for name in names:
            if len(self._visible_descriptors(mode=mode)) >= self._max_model_tools:
                break
            descriptor = self._descriptors_by_name.get(name)
            if (
                descriptor is not None
                and descriptor.exposure == ToolExposure.DEFERRED
                and (mode != "plan" or descriptor.read_only)
            ):
                self._activated_deferred[name] = None
                activated.append(name)
        return activated

    def registered_names(self) -> set[str]:
        return set(self._descriptors_by_name)

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Return immutable descriptor metadata for read-only diagnostics."""
        return self._descriptors

    def replace_source_descriptors(
        self,
        source: str,
        descriptors: Iterable[ToolDescriptor],
    ) -> None:
        replacements = tuple(descriptors)
        if any(descriptor.source != source for descriptor in replacements):
            raise ValueError(f"Replacement descriptor source must be {source!r}")

        existing = list(self._descriptors)
        first_source_index = next(
            (
                index
                for index, descriptor in enumerate(existing)
                if descriptor.source == source
            ),
            len(existing),
        )
        remaining = [
            descriptor for descriptor in existing if descriptor.source != source
        ]
        candidate = (
            remaining[:first_source_index]
            + list(replacements)
            + remaining[first_source_index:]
        )
        build_descriptor_index(candidate)
        ToolRegistry(descriptor.executor for descriptor in candidate)

        activated_identities = {
            self._descriptor_identity(self._descriptors_by_name[name])
            for name in self._activated_deferred
            if name in self._descriptors_by_name
        }
        self._activated_deferred = {}
        self._install_descriptors(tuple(candidate))
        for descriptor in self._descriptors:
            if (
                descriptor.exposure == ToolExposure.DEFERRED
                and self._descriptor_identity(descriptor) in activated_identities
            ):
                self._activated_deferred[descriptor.name] = None

    def _is_model_visible(self, descriptor: ToolDescriptor, *, mode: str) -> bool:
        if mode == "plan" and not descriptor.read_only:
            return False
        if descriptor.exposure == ToolExposure.DIRECT:
            return True
        return (
            descriptor.exposure == ToolExposure.DEFERRED
            and descriptor.name in self._activated_deferred
        )

    def _visible_descriptors(self, *, mode: str) -> list[ToolDescriptor]:
        return [
            descriptor
            for descriptor in self._descriptors
            if self._is_model_visible(descriptor, mode=mode)
        ][: self._max_model_tools]

    def _search_candidates(self, *, mode: str) -> list[ToolDescriptor]:
        return [
            descriptor
            for descriptor in self._descriptors
            if descriptor.exposure == ToolExposure.DEFERRED
            and descriptor.name not in self._activated_deferred
            and (mode != "plan" or descriptor.read_only)
        ]

    def _install_descriptors(
        self, descriptors: tuple[ToolDescriptor, ...]
    ) -> None:
        self._descriptors = descriptors
        self._descriptors_by_name = build_descriptor_index(descriptors)
        self._registry = ToolRegistry(
            descriptor.executor for descriptor in descriptors
        )
        self._activated_deferred = {
            name: None
            for name in self._activated_deferred
            if name in self._descriptors_by_name
        }

    @staticmethod
    def _descriptor_identity(descriptor: ToolDescriptor) -> str:
        return descriptor.identity or f"{descriptor.source}:{descriptor.name}"


class ToolRouterBuilder:
    async def build(
        self,
        *,
        context: ToolDiscoveryContext,
        sync_providers: Iterable[ToolProvider] = (),
        async_providers: Iterable[AsyncToolProvider] = (),
        max_model_tools: int = DEFAULT_MAX_MODEL_TOOLS,
    ) -> ToolRouter:
        descriptors: list[ToolDescriptor] = []
        for provider in sync_providers:
            descriptors.extend(provider.discover(context))

        providers = tuple(async_providers)
        if providers:
            results = await asyncio.gather(
                *(provider.discover(context) for provider in providers),
                return_exceptions=True,
            )
            for provider, result in zip(providers, results, strict=True):
                if isinstance(result, BaseException):
                    logger.warning(
                        "Async tool provider %s failed: %s",
                        provider.__class__.__name__,
                        result,
                    )
                    continue
                descriptors.extend(result)

        return ToolRouter(descriptors, max_model_tools=max_model_tools)


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
