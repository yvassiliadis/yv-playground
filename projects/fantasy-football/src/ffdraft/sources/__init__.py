"""Registry of every pluggable projection source ffdraft knows about.

`ingest.snapshot.run_snapshot` iterates this registry (optionally filtered
to a caller-supplied subset of names) rather than importing each source
module directly, so adding a new source (Task 11 adds five more) never
requires touching the orchestrator.
"""

from __future__ import annotations

from ffdraft.sources.base import ProjectionSource
from ffdraft.sources.cbs import CBSSource
from ffdraft.sources.espn import ESPNSource
from ffdraft.sources.fantasypros import FantasyProsSource
from ffdraft.sources.fantasysharks import FantasySharksSource
from ffdraft.sources.fftoday import FFTodaySource
from ffdraft.sources.nfl import NFLSource
from ffdraft.sources.numberfire import NumberFireSource
from ffdraft.sources.sleeper import SleeperSource

REGISTRY: dict[str, ProjectionSource] = {
    source.name: source
    for source in (
        SleeperSource(),
        FantasyProsSource(),
        ESPNSource(),
        CBSSource(),
        FFTodaySource(),
        FantasySharksSource(),
        NumberFireSource(),
        NFLSource(),
    )
}
