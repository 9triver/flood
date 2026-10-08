"""Application composition for the flood HTTP service.

The HTTP layer used to construct every long lived component as a module global.
Keeping construction in one small object makes the service easier to test and
gives the runtime a single place where dependencies are wired together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    dos_host: Any = None
    dos_playback: Any = None

    @property
    def autonomy_runtime(self):
        return self.dos_playback if self.dos_playback is not None else self.event_runtime

    def close(self) -> None:
        self.app.close_domain_api()
        if self.dos_host is not None:
            self.dos_host.stop()


def build_application(*, runtime: str = "flood") -> ApplicationContext:
    """Create a fully wired application instance.

    Construction order matters: ``AgentRunManager`` and ``EventRuntime`` both
    receive the same ``FloodApp`` facade so they share its repository, agent
    and presentation side effects.
    """

    if runtime not in {"flood", "dos"}:
        raise ValueError(f"Unknown runtime: {runtime}")
    WORKSPACES.begin_session()
    app = FloodApp()
    context = ApplicationContext(
        app=app,
        runs=AgentRunManager(app),
        event_runtime=EventRuntime(app),
        directives=DirectiveStore(),
    )
    if runtime == "dos":
        import os

        from domains.flood.runtime.playback_sources import PlaybackSourceRegistry
        from server.dos_api import DosApi
        from server.dos_host import DosFloodHost, DosPlaybackController

        host = DosFloodHost(fake_model=os.environ.get("DOS_FAKE_MODEL") == "1")
        app.attach_dos_api(DosApi(host))
        context.dos_host = host
        context.dos_playback = DosPlaybackController(host, PlaybackSourceRegistry())
        host.start()
    return context


__all__ = ["ApplicationContext", "build_application"]
