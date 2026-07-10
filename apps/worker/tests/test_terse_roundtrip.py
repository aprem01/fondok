"""Terse round-trip integrity tests (Wave 5 tersefix).

Covers the three corruption modes fixed in BUG 1 plus the shared
``read_extraction_fields`` accessor that BUGs 2 & 3 route every read
through:

  * catalog paths round-trip byte-for-byte;
  * non-catalog paths are stored in their ORIGINAL long form and
    round-trip losslessly (never ``__unknown__``);
  * a 2-segment non-catalog path (e.g. ``foo.adr``) does NOT collide with
    a catalog fid (``adr``);
  * mixed terse + long rows expand per-row;
  * ``read_extraction_fields`` is the identity on long-form input.
"""

from __future__ import annotations

import asyncio

from app.extraction.terse_schema import (
    FIELD_ID_CATALOG,
    FULL_PATH_TO_ID,
    CATALOG_VERSION,
    compress_extraction_result,
    expand_extraction_result,
    field_id_to_name,
    field_name_to_id,
    read_extraction_fields,
)


class TestFieldNameToIdContract:
    def test_catalog_path_returns_fid(self):
        fid = field_name_to_id("p_and_l_usali.operational_kpis.adr_usd")
        assert fid == "adr"

    def test_non_catalog_path_returns_none(self):
        # Was the root cause of BUG 1(a): a non-None auto-generated id
        # kept the long-form fallback dead.
        assert field_name_to_id("unknown.path.not.in.catalog") is None

    def test_two_segment_non_catalog_returns_none_no_collision(self):
        # BUG 1(b): "foo.adr" used to auto-generate fid "adr", colliding
        # with the real catalog entry "adr" and decoding to the WRONG
        # canonical path. It must now return None (stored long-form).
        assert "adr" in FIELD_ID_CATALOG  # the collision target exists
        assert field_name_to_id("foo.adr") is None


class TestCatalogRoundTrip:
    def test_catalog_field_roundtrips(self):
        fields = [
            {
                "field_name": "p_and_l_usali.operating_revenue.rooms_revenue",
                "value": 500000,
                "confidence": 0.95,
                "unit": "USD",
                "source_page": 3,
                "raw_text": "Rooms Revenue 500,000",
            }
        ]
        terse, version = compress_extraction_result(fields)
        assert version == CATALOG_VERSION
        assert terse[0]["fid"] == "rooms_rev"
        assert "field_name" not in terse[0]

        expanded = read_extraction_fields(terse, version)
        assert expanded[0]["field_name"] == fields[0]["field_name"]
        assert expanded[0]["value"] == 500000
        assert expanded[0]["confidence"] == 0.95
        assert expanded[0]["unit"] == "USD"
        assert expanded[0]["source_page"] == 3
        assert expanded[0]["raw_text"] == "Rooms Revenue 500,000"

    def test_every_catalog_path_roundtrips(self):
        for fid, entry in FIELD_ID_CATALOG.items():
            path = entry["full_path"]
            got = field_name_to_id(path)
            assert got == fid, f"{path} -> {got}, expected {fid}"
            assert field_id_to_name(got) == path


class TestNonCatalogStaysLongForm:
    def test_non_catalog_stored_long_form_and_roundtrips(self):
        # BUG 1(c): a genuinely-novel path must survive verbatim, not
        # decay to __unknown__<fid>.
        novel = {
            "field_name": "brand_new_doc.some_section.a_metric",
            "value": 42,
            "confidence": 0.8,
            "unit": "count",
            "source_page": 1,
            "raw_text": "A Metric 42",
        }
        terse, _ = compress_extraction_result([novel])
        # Kept in ORIGINAL long form (no fid), mixed into the same list.
        assert terse[0].get("field_name") == novel["field_name"]
        assert "fid" not in terse[0]

        expanded = read_extraction_fields(terse, CATALOG_VERSION)
        assert expanded[0]["field_name"] == novel["field_name"]
        assert expanded[0]["value"] == 42
        assert not any(
            f["field_name"].startswith("__unknown__") for f in expanded
        )

    def test_no_autogen_fid_collides_with_catalog(self):
        # There are no auto-generated fids anymore, so the compressed
        # output of any non-catalog path can never carry a catalog fid.
        for path in [
            "foo.adr",
            "a.b.gop",
            "x.noi",
            "some.deep.nested.revpar",
        ]:
            terse, _ = compress_extraction_result(
                [{"field_name": path, "value": 1, "confidence": 1.0}]
            )
            assert "fid" not in terse[0]
            # And it round-trips to itself.
            assert read_extraction_fields(terse)[0]["field_name"] == path


class TestMixedRows:
    def test_mixed_terse_and_long_expand_correctly(self):
        mixed = [
            {"fid": "rooms_rev", "v": 500000, "c": 0.95, "u": "USD"},
            {
                "field_name": "custom.non_catalog.metric",
                "value": 7,
                "confidence": 0.6,
            },
            {"fid": "occ_pct", "v": 0.75, "c": 0.9},
        ]
        expanded = read_extraction_fields(mixed, CATALOG_VERSION)
        assert (
            expanded[0]["field_name"]
            == "p_and_l_usali.operating_revenue.rooms_revenue"
        )
        assert expanded[0]["value"] == 500000
        # Long-form row passed through untouched.
        assert expanded[1] is mixed[1]
        assert expanded[1]["field_name"] == "custom.non_catalog.metric"
        assert (
            expanded[2]["field_name"]
            == "p_and_l_usali.operational_kpis.occupancy_pct"
        )
        assert expanded[2]["value"] == 0.75

    def test_compress_then_read_on_mixed_catalog_and_novel(self):
        fields = [
            {
                "field_name": "p_and_l_usali.net_operating_income_usd",
                "value": 1_000_000,
                "confidence": 0.99,
            },
            {
                "field_name": "novel.thing.here",
                "value": 5,
                "confidence": 0.5,
            },
        ]
        terse, _ = compress_extraction_result(fields)
        assert terse[0]["fid"] == "noi"           # catalog -> compressed
        assert terse[1].get("field_name") == "novel.thing.here"  # kept long

        back = read_extraction_fields(terse)
        assert back[0]["field_name"] == "p_and_l_usali.net_operating_income_usd"
        assert back[1]["field_name"] == "novel.thing.here"


class TestReadAccessorIdentity:
    def test_identity_on_long_form(self):
        long_form = [
            {"field_name": "p_and_l_usali.gross_operating_profit_usd", "value": 3},
            {"field_name": "property_overview.name", "value": "Hotel X"},
        ]
        out = read_extraction_fields(long_form)
        # Flag-OFF default is a true no-op: same object returned.
        assert out is long_form

    def test_empty_and_none(self):
        assert read_extraction_fields([]) == []
        assert read_extraction_fields(None) == []

    def test_async_wrapper_matches_sync(self):
        terse = [{"fid": "adr", "v": 300, "c": 0.9}]
        sync_out = read_extraction_fields(terse, CATALOG_VERSION)
        async_out = asyncio.run(expand_extraction_result(terse, CATALOG_VERSION))
        assert sync_out == async_out


__all__ = [
    "TestFieldNameToIdContract",
    "TestCatalogRoundTrip",
    "TestNonCatalogStaysLongForm",
    "TestMixedRows",
    "TestReadAccessorIdentity",
]
