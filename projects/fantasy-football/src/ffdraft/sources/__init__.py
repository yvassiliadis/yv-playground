"""Registry of every pluggable projection source ffdraft knows about.

`ingest.snapshot.run_snapshot` iterates this registry (optionally filtered
to a caller-supplied subset of names) rather than importing each source
module directly, so adding a new source (Task 11 adds five more) never
requires touching the orchestrator.
"""

from __future__ import annotations

from ffdraft.sources.base import ProjectionSource
from ffdraft.sources.espn import ESPNSource
from ffdraft.sources.fantasypros import FantasyProsSource
from ffdraft.sources.sleeper import SleeperSource

REGISTRY: dict[str, ProjectionSource] = {
    source.name: source
    for source in (SleeperSource(), FantasyProsSource(), ESPNSource())
}
