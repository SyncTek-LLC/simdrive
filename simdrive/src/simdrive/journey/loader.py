"""Journey + persona file discovery and batch loading.

Provides helpers for the CI orchestrator and the `simdrive validate` CLI
to discover and validate all journeys/personas in a project directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .errors import ci_invalid_journey
from .persona import Persona, load_persona
from .schema import Journey, load_journey

# Default simdrive project structure relative to the caller's cwd.
_DEFAULT_JOURNEYS_DIR = ".simdrive/journeys"
_DEFAULT_PERSONAS_DIR = ".simdrive/personas"


def discover_journey_paths(journeys_dir: str | Path) -> list[Path]:
    """Return all *.yaml files in journeys_dir, sorted by name."""
    journeys_dir = Path(journeys_dir)
    return sorted(journeys_dir.glob("*.yaml"))


def iter_journeys(
    journeys_dir: str | Path,
    personas_dir: str | Path | None = None,
    tag_filter: list[str] | None = None,
    slug_filter: list[str] | None = None,
) -> Iterator[Journey]:
    """Yield validated Journey objects from journeys_dir.

    Raises SimdriveError (ci_invalid_journey) on the first invalid journey
    so the CI orchestrator can bail early rather than accumulate errors.

    tag_filter: if non-empty, only yield journeys whose tags overlap.
    slug_filter: if non-empty, only yield journeys whose name matches.
    """
    for path in discover_journey_paths(journeys_dir):
        # Load and validate — propagate SimdriveError for invalid files.
        try:
            journey = load_journey(path, personas_dir=personas_dir)
        except Exception as exc:
            raise ci_invalid_journey(str(path), str(exc)) from exc

        # Tag filter
        if tag_filter:
            if not set(journey.tags).intersection(tag_filter):
                continue

        # Slug / explicit name filter
        if slug_filter:
            if journey.name not in slug_filter:
                continue

        yield journey


def load_persona_for_journey(journey: Journey, personas_dir: str | Path) -> Persona:
    """Load the persona referenced by this journey."""
    personas_dir = Path(personas_dir)
    persona_path = personas_dir / f"{journey.persona}.yaml"
    return load_persona(persona_path)
