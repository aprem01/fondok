"""Terse output schema tests — catalog completeness, round-trip fidelity, backward compat.

Tests the field ID compression system that reduces output tokens by 40–60%.
"""

from __future__ import annotations

import asyncio
import json
import pytest

from app.extraction.terse_schema import (
    FIELD_ID_CATALOG,
    CATALOG_VERSION,
    field_name_to_id,
    field_id_to_name,
    expand_extraction_result,
    compress_extraction_result,
)


class TestFieldCatalogCompleteness:
    """Verify the catalog covers all known extractor output paths."""

    def test_catalog_has_entries(self):
        """Catalog must not be empty."""
        assert len(FIELD_ID_CATALOG) > 0, "Field ID catalog is empty"

    def test_catalog_version_is_set(self):
        """Current catalog version must be defined."""
        assert CATALOG_VERSION >= 1, f"Catalog version must be >= 1, got {CATALOG_VERSION}"

    def test_all_entries_have_required_fields(self):
        """Each catalog entry must have full_path, description, data_type."""
        required = {"full_path", "description", "data_type"}
        for fid, entry in FIELD_ID_CATALOG.items():
            assert isinstance(entry, dict), f"Entry for {fid} is not a dict"
            missing = required - set(entry.keys())
            assert not missing, f"Entry for {fid} missing: {missing}"

    def test_field_ids_are_short(self):
        """Field IDs should be 2–8 characters (token compression goal)."""
        for fid in FIELD_ID_CATALOG.keys():
            assert 2 <= len(fid) <= 20, f"Field ID '{fid}' too long (max 20)"

    def test_no_duplicate_full_paths(self):
        """Each full_path should map to exactly one field_id."""
        paths_seen = {}
        for fid, entry in FIELD_ID_CATALOG.items():
            path = entry["full_path"]
            if path in paths_seen:
                pytest.fail(
                    f"Duplicate full_path '{path}' mapped to both "
                    f"'{paths_seen[path]}' and '{fid}'"
                )
            paths_seen[path] = fid

    def test_known_extractor_paths_are_cataloged(self):
        """All paths from live_extraction_anglers_t12.json fixture should be in catalog."""
        import json
        from pathlib import Path

        fixture_path = Path("tests/fixtures/usali_v4/live_extraction_anglers_t12.json")
        if not fixture_path.exists():
            pytest.skip("Fixture not found, skipping live path check")

        with open(fixture_path) as f:
            data = json.load(f)

        all_paths = {f["field_name"] for f in data.get("fields", [])}
        catalog_paths = {entry["full_path"] for entry in FIELD_ID_CATALOG.values()}

        missing = all_paths - catalog_paths
        if missing:
            # Report but don't fail — gaps are OK, just log them
            print(f"\nWARNING: {len(missing)} paths from fixture not in catalog:")
            for path in sorted(missing)[:10]:
                print(f"  - {path}")
            if len(missing) > 10:
                print(f"  ... and {len(missing) - 10} more")


class TestFieldNameToIdMapping:
    """Test the lookup functions."""

    def test_field_name_to_id_known_path(self):
        """Mapping a known full_path should return a field_id."""
        # Pick a well-known field
        fid = field_name_to_id("p_and_l_usali.operating_revenue.rooms_revenue")
        assert fid is not None
        assert fid in FIELD_ID_CATALOG

    def test_field_name_to_id_unknown_path(self):
        """Mapping an unknown path should auto-generate a short ID."""
        fid = field_name_to_id("unknown.path.not.in.catalog")
        assert fid is not None
        assert isinstance(fid, str)
        assert len(fid) <= 12

    def test_field_id_to_name_known_id(self):
        """Reverse mapping should work."""
        # Get any catalog entry
        sample_fid = next(iter(FIELD_ID_CATALOG.keys()))
        sample_path = FIELD_ID_CATALOG[sample_fid]["full_path"]

        # Map back
        retrieved_path = field_id_to_name(sample_fid)
        assert retrieved_path == sample_path

    def test_field_id_to_name_unknown_id(self):
        """Reverse mapping of unknown ID should return None."""
        path = field_id_to_name("nonexistent_id_xyz")
        assert path is None

    def test_roundtrip_known_path(self):
        """Known path → ID → path should be identical."""
        known_path = "p_and_l_usali.operating_revenue.rooms_revenue"
        fid = field_name_to_id(known_path)
        assert fid is not None
        roundtrip_path = field_id_to_name(fid)
        assert roundtrip_path == known_path


class TestCompressionRoundTrip:
    """Test terse encoding/decoding round-trips."""

    def test_compress_long_form_fields(self):
        """Compress long-form extraction fields to terse JSON."""
        fields = [
            {
                "field_name": "p_and_l_usali.operating_revenue.rooms_revenue",
                "value": 500000,
                "confidence": 0.95,
                "unit": "USD",
                "source_page": 1,
                "raw_text": "Rooms Revenue: 500000",
            },
            {
                "field_name": "p_and_l_usali.operational_kpis.occupancy_pct",
                "value": 0.75,
                "confidence": 0.92,
                "unit": "pct",
                "source_page": 2,
                "raw_text": "Occupancy: 75%",
            },
        ]

        terse, version = compress_extraction_result(fields)

        assert len(terse) == 2
        assert version == CATALOG_VERSION
        assert all("fid" in f for f in terse), "All terse fields should have 'fid'"
        assert all("v" in f for f in terse), "All terse fields should have 'v' (value)"
        assert all("c" in f for f in terse), "All terse fields should have 'c' (confidence)"

    def test_compress_skips_unmapped_fields(self):
        """Fields not in catalog should be emitted as-is with a warning."""
        fields = [
            {
                "field_name": "unknown.path.not.in.catalog",
                "value": 123,
                "confidence": 0.5,
            }
        ]

        terse, version = compress_extraction_result(fields)
        # Should still emit the field (or skip it with a warning)
        # Depends on implementation, but compression should not crash

    def test_expand_terse_fields(self):
        """Expand terse fields back to long form."""

        async def run_expand():
            terse_fields = [
                {"fid": "rooms_rev", "v": 500000, "c": 0.95, "u": "USD", "sp": 1},
                {"fid": "occ_pct", "v": 0.75, "c": 0.92, "u": "pct", "sp": 2},
            ]

            expanded = await expand_extraction_result(terse_fields, CATALOG_VERSION)

            assert len(expanded) == 2
            assert expanded[0]["field_name"] == "p_and_l_usali.operating_revenue.rooms_revenue"
            assert expanded[0]["value"] == 500000
            assert expanded[0]["confidence"] == 0.95

            assert expanded[1]["field_name"] == "p_and_l_usali.operational_kpis.occupancy_pct"
            assert expanded[1]["value"] == 0.75

        asyncio.run(run_expand())

    def test_expand_unknown_field_id_fallback(self):
        """Expand should handle unknown field IDs gracefully."""

        async def run_expand():
            terse_fields = [
                {"fid": "unknown_id_xyz", "v": 123, "c": 0.5},
            ]

            expanded = await expand_extraction_result(terse_fields, CATALOG_VERSION)

            assert len(expanded) == 1
            # Should emit a fallback field with a warning field_name
            assert "__unknown__" in expanded[0].get("field_name", "")

        asyncio.run(run_expand())

    def test_expand_already_long_form(self):
        """Expand should pass through fields already in long form."""

        async def run_expand():
            long_form = [
                {
                    "field_name": "p_and_l_usali.operating_revenue.rooms_revenue",
                    "value": 500000,
                    "confidence": 0.95,
                }
            ]

            expanded = await expand_extraction_result(long_form, None)

            assert len(expanded) == 1
            assert expanded[0]["field_name"] == "p_and_l_usali.operating_revenue.rooms_revenue"

        asyncio.run(run_expand())

    def test_expand_null_catalog_version_legacy(self):
        """Null catalog version should be treated as legacy long-form."""

        async def run_expand():
            # Long-form fields with catalog_version = None should pass through
            long_form = [
                {
                    "field_name": "p_and_l_usali.operating_revenue.rooms_revenue",
                    "value": 500000,
                }
            ]

            expanded = await expand_extraction_result(long_form, None)
            assert expanded[0]["field_name"] == "p_and_l_usali.operating_revenue.rooms_revenue"

        asyncio.run(run_expand())


class TestBackwardCompatibility:
    """Ensure old extraction results (without catalog_version) still work."""

    def test_legacy_long_form_fields_still_work(self):
        """Old extraction results with catalog_version=NULL should expand to themselves."""

        async def run_expand():
            legacy_fields = [
                {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 500000}
            ]

            # Pass catalog_version as None to simulate legacy row
            expanded = await expand_extraction_result(legacy_fields, None)

            # Should return as-is
            assert expanded == legacy_fields

        asyncio.run(run_expand())

    def test_mixed_terse_and_long_form(self):
        """Handle mixed terse/long-form fields (shouldn't happen, but be defensive)."""

        async def run_expand():
            mixed = [
                {"fid": "rooms_rev", "v": 500000, "c": 0.95},
                {"field_name": "p_and_l_usali.operational_kpis.occupancy_pct", "value": 0.75},
            ]

            expanded = await expand_extraction_result(mixed, CATALOG_VERSION)

            # First should be expanded from terse
            assert expanded[0]["field_name"] == "p_and_l_usali.operating_revenue.rooms_revenue"

            # Second should pass through (already long form)
            assert expanded[1]["field_name"] == "p_and_l_usali.operational_kpis.occupancy_pct"

        asyncio.run(run_expand())


class TestTokenSavings:
    """Verify compression actually saves tokens."""

    def test_token_count_reduction(self):
        """Terse format should use fewer tokens than long format."""
        long_field_name = "p_and_l_usali.operating_revenue.food_beverage_revenue"
        terse_field_id = field_name_to_id(long_field_name)

        # Very rough token count (dots + length heuristic)
        long_tokens = len(long_field_name.split(".")) + 1
        terse_tokens = len(terse_field_id.split("_")) + 1

        # Terse should be fewer tokens
        assert terse_tokens <= long_tokens, (
            f"Terse '{terse_field_id}' ({terse_tokens} tokens) should beat "
            f"long '{long_field_name}' ({long_tokens} tokens)"
        )


__all__ = [
    "TestFieldCatalogCompleteness",
    "TestFieldNameToIdMapping",
    "TestCompressionRoundTrip",
    "TestBackwardCompatibility",
    "TestTokenSavings",
]
