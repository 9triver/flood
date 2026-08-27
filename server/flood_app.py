from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, TYPE_CHECKING

from domain_os import (
    DomainControlModel,
    DomainControlService,
    DomainQueryService,
    DomainReadModel,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DOMAIN_DIR = PROJECT_DIR / "domains" / "flood"

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from oag.ontology.loader import load_domain  # noqa: E402
from domains.flood.runtime.impact_analysis import (  # noqa: E402
    BRIDGE_INFLUENCE_RADIUS_M,
)

from server.chat.agent_factory import (  # noqa: E402
    FloodAgentFactory,
    compact_agent_query_result,
    configured_agent_max_turns,
    load_env,
)
from server.chat.policy import build_agent_task_hint  # noqa: E402
from server.chat.service import FloodChatService  # noqa: E402
from server.chat.side_effects import AgentSideEffects  # noqa: E402
from server.domain_service import FloodDomainService  # noqa: E402
from server.domain_api import DomainApi, DomainApiUnavailable  # noqa: E402
from server.domain_tools import (  # noqa: E402
    build_domain_event_context,
    register_domain_query_tools,
)

if TYPE_CHECKING:
    from server.agent_runs import AgentRun


DomainCommandRunner = Callable[
    [Callable[[], Awaitable[dict[str, Any]]]],
    dict[str, Any],
]


class FloodApp:
    """Stable application facade for HTTP and autonomous event runtimes."""

    def __init__(self, domain_runtime: DomainReadModel | None = None):
        self.llm_config = load_env(PROJECT_DIR / ".env")
        self.ontology, self.repository, self.registry = load_domain(DOMAIN_DIR)
        self.resolver = self.registry.get_resolver("flood_repository")

        self._domain_service = FloodDomainService(
            self.ontology, self.registry, self.resolver,
        )
        self.side_effects = AgentSideEffects(
            self.ontology.presentation_tools,
        )
        self._agent_factory = FloodAgentFactory(
            self.llm_config, PROJECT_DIR,
        )
        self.llm_client = self._agent_factory.build_llm_client()
        self.agent = self._agent_factory.build_agent(
            llm_client=self.llm_client,
            ontology=self.ontology,
            repository=self.repository,
            registry=self.registry,
            resolver=self.resolver,
            post_tool_call=self.side_effects.capture_tool_event,
        )
        self._chat_service = FloodChatService(
            self.agent, self.ontology, self.side_effects,
        )
        self._domain_api: DomainApi | None = None
        self._domain_control: DomainControlService | None = None
        self._domain_command_runner: DomainCommandRunner | None = None
        if domain_runtime is not None:
            self.attach_domain_runtime(domain_runtime)

    @property
    def llm_enabled(self) -> bool:
        return self._agent_factory.enabled

    def bootstrap(self) -> dict[str, Any]:
        return {
            **self._domain_service.bootstrap(llm_enabled=self.llm_enabled),
            "domain_os_query_enabled": self._domain_api is not None,
            "domain_os_control_enabled": self._domain_control is not None,
        }

    def autonomy_cycle(self, force_forecast: bool = False) -> dict:
        return self._domain_service.autonomy_cycle(force_forecast)

    def forecast(self, force: bool = False) -> dict:
        return self._domain_service.forecast(force)

    def export_geojson(self, object_type: str, filters: dict,
                       simplify: float = 0) -> tuple[dict, bytes]:
        return self._domain_service.export_geojson(
            object_type, filters, simplify,
        )

    def hydrodynamic_grid_stats(
        self,
        forecast_id: str = "latest",
        *,
        domain_product_id: str | None = None,
    ) -> dict[str, Any]:
        if domain_product_id:
            return self.domain_api.views.forecast_grid_meta(domain_product_id)
        return self._domain_service.hydrodynamic_grid_stats(forecast_id)

    def hydrodynamic_grid_tile(
        self,
        z: int,
        x: int,
        y: int,
        forecast_id: str = "latest",
        wet_only: bool = False,
        time_h: float | None = None,
        tile_crs: str = "wgs84",
        *,
        domain_product_id: str | None = None,
    ) -> dict[str, Any]:
        if domain_product_id:
            return self.domain_api.views.forecast_grid_tile(
                z,
                x,
                y,
                domain_product_id,
                wet_only=wet_only,
                time_h=time_h,
                tile_crs=tile_crs,
            )
        return self._domain_service.hydrodynamic_grid_tile(
            z, x, y, forecast_id, wet_only, time_h, tile_crs,
        )

    def analyze_inundation_impacts(
        self,
        forecast_id: str = "latest",
        target_type: str = "all",
        min_depth_m: float = 0.15,
        max_distance_m: float = 10.0,
        time_h: float | None = None,
        bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
        *,
        assessment_product_id: str | None = None,
        forecast_product_id: str | None = None,
    ) -> dict[str, Any]:
        if assessment_product_id:
            return self.domain_api.views.impact_assessment(
                assessment_product_id,
            )
        if forecast_product_id:
            return self.domain_api.views.impact_for_forecast(
                forecast_product_id,
                target_type=target_type,
                min_depth_m=min_depth_m,
                max_distance_m=max_distance_m,
                bridge_influence_radius_m=bridge_influence_radius_m,
                time_h=time_h,
            )
        return self._domain_service.analyze_inundation_impacts(
            forecast_id,
            target_type,
            min_depth_m,
            max_distance_m,
            time_h,
            bridge_influence_radius_m,
        )

    def get_object(self, object_type: str, object_id: str) -> dict[str, Any]:
        return self._domain_service.get_object(object_type, object_id)

    def stream_chat(self, run: AgentRun) -> None:
        self._chat_service.stream_chat(run)

    def agent_session_id(self, session_id: str) -> str:
        return self._chat_service.agent_session_id(session_id)

    @property
    def domain_api(self) -> DomainApi:
        if self._domain_api is None:
            raise DomainApiUnavailable("Domain OS query API is not configured")
        return self._domain_api

    def attach_dos_api(self, api) -> None:
        """Swap in a dos-backed adapter (duck-typed like DomainApi)."""
        if self._domain_api is not None:
            self._domain_api.close()
        self._domain_api = api

    def attach_domain_runtime(
        self,
        runtime: DomainReadModel,
        *,
        command_runner: DomainCommandRunner | None = None,
        control_target: DomainControlModel | None = None,
    ) -> None:
        if self._domain_api is not None:
            self._domain_api.close()
        self._domain_api = DomainApi(DomainQueryService(runtime))
        self._domain_control = (
            DomainControlService(control_target or runtime)
            if command_runner is not None
            else None
        )
        self._domain_command_runner = command_runner
        if self.agent is not None:
            register_domain_query_tools(
                self.agent.harness.tools,
                lambda: self.domain_api.queries,
            )
            self.agent.harness.config.runtime_context["domain_os"] = (
                "read-only Projection/DerivedProduct/Domain Event access enabled; "
                "use concrete product IDs and preserve validity and lineage"
            )

    def domain_event_context(
        self,
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._domain_api is None:
            return None
        return build_domain_event_context(self._domain_api.queries, event)

    def submit_domain_intent(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._run_domain_command(
            lambda: self._require_domain_control().submit_intent(payload),
        )

    def approve_domain_command(
        self,
        command_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._run_domain_command(
            lambda: self._require_domain_control().approve(command_id, payload),
        )

    def reject_domain_command(
        self,
        command_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._run_domain_command(
            lambda: self._require_domain_control().reject(command_id, payload),
        )

    def _require_domain_control(self) -> DomainControlService:
        if self._domain_control is None:
            raise DomainApiUnavailable("Domain OS control API is not configured")
        return self._domain_control

    def _run_domain_command(
        self,
        operation: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        runner = self._domain_command_runner
        if runner is None:
            raise DomainApiUnavailable("Domain OS control API is not configured")
        return runner(operation)

    def close_domain_api(self) -> None:
        if self._domain_api is None:
            return
        self._domain_api.close()
        self._domain_api = None
        self._domain_control = None
        self._domain_command_runner = None


__all__ = [
    "FloodApp",
    "build_agent_task_hint",
    "compact_agent_query_result",
    "configured_agent_max_turns",
]
