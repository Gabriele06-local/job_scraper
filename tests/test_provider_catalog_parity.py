"""Guard the single source of truth for provider identity.

The connector REGISTRY (code), the seed catalog (`_SEED` → `providers`
collection) and runtime telemetry (`import_runs.provider_slug`) must all key on
the SAME slug, and the catalog's display name must match the connector's
`source_name`. These tests fail the build if the two hand-maintained lists
drift, so the dashboard's /reports/providers and /reports/sources always
describe the same entities.
"""

from __future__ import annotations

from connectors import REGISTRY
from scripts.seed_providers import _SEED


def test_registry_and_seed_share_the_same_slugs() -> None:
    registry_slugs = set(REGISTRY.keys())
    seed_slugs = {p.slug for p in _SEED}
    missing_in_seed = registry_slugs - seed_slugs
    missing_in_registry = seed_slugs - registry_slugs
    assert not missing_in_seed, f"connectors with no catalog entry: {sorted(missing_in_seed)}"
    assert not missing_in_registry, (
        f"catalog entries with no connector: {sorted(missing_in_registry)}"
    )


def test_catalog_name_matches_connector_source_name() -> None:
    mismatches = {
        p.slug: (p.name, REGISTRY[p.slug].cls.source_name)
        for p in _SEED
        if p.name != REGISTRY[p.slug].cls.source_name
    }
    assert not mismatches, f"catalog name != connector source_name: {mismatches}"


def test_seed_slugs_are_unique() -> None:
    slugs = [p.slug for p in _SEED]
    assert len(slugs) == len(set(slugs)), "duplicate slug in provider catalog"
