"""Application composition for the flood HTTP service.

The HTTP layer used to construct every long lived component as a module global.
Keeping construction in one small object makes the service easier to test and
gives the runtime a single place where dependencies are wired together.
"""

from __future__ import annotations

from dataclasses import dataclass

from domains.flood.runtime.workspace import WORKSPACES
from server.agent_runs import AgentRunManager
from server.directives import DirectiveStore
from server.events import EventRuntime
from server.flood_app import FloodApp


@dataclass(slots=True)
class ApplicationContext:
    """Long lived services owned by one HTTP application instance."""

    app: FloodApp
    runs: AgentRunManager
    event_runtime: EventRuntime
    directives: DirectiveStore


def build_application() -> ApplicationContext:
    """Create a fully wired application instance.

    Construction order matters: ``AgentRunManager`` and ``EventRuntime`` both
    receive the same ``FloodApp`` facade so they share its repository, agent
    and presentation side effects.
    """

    WORKSPACES.begin_session()
    app = FloodApp()
    return ApplicationContext(
        app=app,
        runs=AgentRunManager(app),
        event_runtime=EventRuntime(app),
        directives=DirectiveStore(),
    )


__all__ = ["ApplicationContext", "build_application"]
