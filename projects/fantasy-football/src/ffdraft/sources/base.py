"""Shared framework for pluggable projection sources.

`ProjectionSource` is the interface every concrete source module (Sleeper,
FantasyPros, ESPN today; five more land in Task 11) implements. Each source
is responsible for its own HTTP fetch and parsing, but they share two small
pieces of plumbing defined here:

- `make_http_client`: a single place to set a browser-like `User-Agent`
  (some of these unofficial/public endpoints reject the default httpx UA)
  and a sane timeout. Deliberately not a bigger "HTTP framework" -- a plain
  factory function is enough for eight sources that each just need a GET.
- `resolve_player_id_by_native_id`: the crosswalk join every source with a
  native ID (Sleeper, ESPN, FantasyPros' JSON endpoints) should prefer over
  name-matching. Both sides of the join are cast to `Utf8` before joining,
  since `ids.crosswalk.build_crosswalk()`'s ID columns come back as Int64
  from nflreadpy while a source's native ID often arrives as a JSON string
  (or vice versa) -- casting both to string sidesteps that mismatch without
  either side needing to know the other's exact dtype.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx
import polars as pl

from ffdraft.ingest.actuals import CANONICAL_COLUMNS

__all__ = [
    "CANONICAL_COLUMNS",
    "ProjectionSource",
    "make_http_client",
    "resolve_player_id_by_native_id",
]

# A plain requests/httpx default UA gets a 403 or a stripped-down response
# from more than one of these unofficial APIs; a common desktop-Chrome UA
# string reliably gets the same response a browser would.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 15.0


def make_http_client(**kwargs: object) -> httpx.Client:
    """Build an `httpx.Client` with a browser-like User-Agent and a timeout.

    `kwargs` are forwarded to `httpx.Client` (e.g. a source can pass its own
    extra `headers` to merge in, or override `timeout`); a caller-supplied
    `headers` dict is merged on top of the default UA rather than replacing
    it outright.
    """
    headers = {"User-Agent": _BROWSER_USER_AGENT}
    headers.update(kwargs.pop("headers", None) or {})
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    follow_redirects = kwargs.pop("follow_redirects", True)
    return httpx.Client(
        headers=headers, timeout=timeout, follow_redirects=follow_redirects, **kwargs
    )


@runtime_checkable
class ProjectionSource(Protocol):
    """Interface every projection source implements.

    `fetch` returns data already in the canonical long schema (see
    `ffdraft.ingest.actuals.CANONICAL_COLUMNS`) with `source` set to
    `self.name`, `week=0` for a seasonal projection, and `snapshot_date` set
    to the time of the fetch.
    """

    name: str

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame: ...


def resolve_player_id_by_native_id(
    df: pl.DataFrame,
    reference: pl.DataFrame,
    crosswalk_id_column: str,
    native_id_column: str = "source_player_id",
) -> pl.DataFrame:
    """Left-join `df`'s native ID column against `build_crosswalk()`'s output.

    Adds/overwrites a `player_id` column on `df`. Rows whose native ID has
    no match in `reference` (including any row whose native ID is null) get
    `player_id = None`, exactly like `ids.crosswalk.resolve_by_name`'s
    genuine-non-match case -- callers can collect those the same way (filter
    on `player_id.is_null()`), so an ID-join source and a name-matched
    source produce unmatched rows the same shape.
    """
    ref = (
        reference.select(
            pl.col(crosswalk_id_column).cast(pl.Utf8).alias("_native_id"),
            pl.col("player_id"),
        )
        .drop_nulls("_native_id")
        .unique(subset=["_native_id"], keep="first")
    )
    return (
        df.with_columns(pl.col(native_id_column).cast(pl.Utf8).alias("_native_id"))
        .join(ref, on="_native_id", how="left")
        .drop("_native_id")
    )
