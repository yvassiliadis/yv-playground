"""League configuration: team count, recency decay, and roster shape.

Single source of truth for the numbers that `metrics/vor.py` and
`metrics/consistency.py` both need but that neither owns: how many teams are
in the league, how fast historical seasons decay in weight, and how a roster
is built (starters per position, bench size). `model/calibrate.py` has its
own `RECENCY_LAMBDA` for LOSO fold weighting -- `DECAY_LAMBDA` here is the
same value, kept independent so board-building code does not have to import
model internals just to read a constant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

#: Default league size. `metrics.vor.compute_vor` uses this to size the ADP
#: pool it draws `N_pos` from (see `vor.py`'s module docstring).
TEAMS = 10

#: Recency decay for weighting historical seasons. Mirrors
#: `ffdraft.model.calibrate.RECENCY_LAMBDA` (~half weight after 2 seasons).
DECAY_LAMBDA = 0.35

_DEFAULT_STARTERS = MappingProxyType(
    {
        "QB": 1,
        "RB": 2,
        "WR": 2,
        "TE": 1,
        "FLEX": 1,
        "K": 1,
        "DST": 1,
    }
)


@dataclass(frozen=True)
class RosterConfig:
    """Starters-per-position plus bench size for one team.

    `starters` intentionally includes a `FLEX` slot as its own key rather
    than splitting it across RB/WR/TE -- `roster_math_replacement` documents
    how (and how coarsely) it handles FLEX.

    `starters` is a `MappingProxyType`, not a plain `dict`: `frozen=True`
    only stops reassigning the attribute, not mutating a mutable value
    stored in it, so a plain `dict` field here would make the freeze (and
    hashing, since a `dict`-holding dataclass raises on `hash()`) illusory.
    """

    starters: MappingProxyType[str, int] = field(
        default_factory=lambda: _DEFAULT_STARTERS
    )
    bench: int = 6

    def starters_at(self, position: str) -> int:
        """Starters for `position`, or 0 if it has no starting slot."""
        return self.starters.get(position, 0)


#: The league's roster shape. A single instance so callers share one
#: definition rather than each constructing their own defaults.
DEFAULT_ROSTER_CONFIG = RosterConfig()
