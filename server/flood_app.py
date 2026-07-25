from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TYPE_CHECKING


PROJECT_DIR = Path(__file__).resolve().parents[1]
DOMAIN_DIR = PROJECT_DIR / "domains" / "flood"

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from oag.ontology.loader import load_domain  # noqa: E402

from server.chat.agent_factory import (  # noqa: E402
    FloodAgentFactory,
    compact_agent_query_result,
    configured_agent_max_turns,
    load_env,
)
from server.chat.policy import (  # noqa: E402
    build_agent_task_hint,
    select_user_agent_tools,
)
from server.chat.service import FloodChatService  # noqa: E402
from server.chat.side_effects import AgentSideEffects  # noqa: E402
from server.domain_service import FloodDomainService  # noqa: E402

if TYPE_CHECKING:
    from server.agent_runs import AgentRun


class FloodApp:
    """Stable application facade for HTTP and autonomous event runtimes."""

    def __init__(self):
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

    @property
    def llm_enabled(self) -> bool:
        return self._agent_factory.enabled

    def bootstrap(self) -> dict[str, Any]:
        return self._domain_service.bootstrap(llm_enabled=self.llm_enabled)

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
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        return self._domain_service.analyze_inundation_impacts(
            forecast_id,
            target_type,
            min_depth_m,
            max_distance_m,
            time_h,
        )

    def get_object(self, object_type: str, object_id: str) -> dict[str, Any]:
        return self._domain_service.get_object(object_type, object_id)

    def stream_chat(self, run: AgentRun) -> None:
        self._chat_service.stream_chat(run)

    def agent_session_id(self, session_id: str) -> str:
        return self._chat_service.agent_session_id(session_id)


__all__ = [
    "FloodApp",
    "build_agent_task_hint",
    "compact_agent_query_result",
    "configured_agent_max_turns",
    "select_user_agent_tools",
]
