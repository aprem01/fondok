"""A9 (go-live audit) — the segment model's platform defaults are badged in
the exports and never labelled as STR data."""

from __future__ import annotations

from openpyxl import Workbook


def _sources() -> dict[str, str]:
    src: dict[str, str] = {}
    for seg in ("transient_bar", "transient_ota", "corporate", "group", "contract"):
        src[f"segments.{seg}.channel_cost_pct"] = "platform_default_segmentation"
        src[f"segments.{seg}.mix_pct"] = "platform_default_segmentation"
        src[f"segments.{seg}.adr"] = "platform_default_segmentation"
    src["segments.group.mix_pct"] = "str_segmentation"
    src["segments.group.adr"] = "str_segmentation"
    return src


def test_segment_provenance_names_platform_defaults() -> None:
    from app.export.live_payload import _build_segment_provenance

    prov = _build_segment_provenance({"__sources__": _sources()})
    assert prov is not None
    assert "group mix" in prov["str_segmentation_fields"]
    assert "transient ota channel cost" in prov["platform_default_fields"]
    assert prov["note"].startswith("Platform default — no segmentation data on file: ")
    assert "STR" not in prov["note"]
    assert _build_segment_provenance({"__sources__": {"exit_cap_rate": "seed"}}) is None


def test_revenue_mix_sheet_and_memo_print_the_badge() -> None:
    from app.export.excel import _build_revenue_mix
    from app.export.live_payload import _build_segment_provenance
    from app.export.memo_pdf import _render_revenue_mix

    prov = _build_segment_provenance({"__sources__": _sources()})
    seg = {"name": "group", "mix_pct": 0.2, "adr": 200.0, "gross_revenue": 1.0,
           "channel_cost_pct": 0.05, "net_revenue": 1.0}
    wb = Workbook()
    assert _build_revenue_mix(wb, [{"year": 1, "segment_breakdown": [seg]}], prov)
    ws = wb["Revenue Mix"]
    cells = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
    assert any(v.startswith("Platform default — no segmentation data on file") for v in cells)
    html_out = _render_revenue_mix([seg], prov)
    assert "Platform default — no segmentation data on file" in html_out


def test_no_export_label_calls_a_platform_default_str() -> None:
    from app.export.labels import SOURCE_DISPLAY
    from app.ontology.registry import load_registry

    for key in ("platform_default_segmentation", "str_segmentation_default"):
        assert "STR" not in SOURCE_DISPLAY[key], key
        src = load_registry().sources[key]
        assert "STR" not in src.label and "STR" not in src.badge, key
        assert src.kind == "assumption"
    assert load_registry().sources["platform_default_segmentation"].badge == (
        "Platform default — no segmentation data on file"
    )
    assert load_registry().sources["str_segmentation"].kind == "grounded"
