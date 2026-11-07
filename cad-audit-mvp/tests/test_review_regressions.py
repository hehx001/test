"""Reproduce issues from independent engineering review (2026-11-07).

Control cases are deliberately altered CAD models, not claims that published
reference drawings contain manufacturing design defects.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import cadquery as cq
import ezdxf
import pytest
from fastapi.testclient import TestClient

from cad_audit.api import app
from cad_audit.dimension_rules import check_dimension
from cad_audit.engine import inspect_bom, inspect_dxf, inspect_step
from cad_audit.models import (
    ModelConfigurationError, _compact_report, _configuration, _filter_ai_review,
)


def make_dim_dxf(path, display, *, dimlfac=1, unit_code=4):
    doc = ezdxf.new("R2010")
    doc.units = unit_code
    doc.dimstyles.new("PROOF", dxfattribs={"dimlfac": dimlfac})
    dim = doc.modelspace().add_linear_dim(
        base=(0, 10), p1=(0, 0), p2=(100, 0),
        dimstyle="PROOF", text=display,
    )
    dim.render()
    doc.saveas(path)
    return path


@pytest.mark.parametrize("shown,expected", [
    ("200", "numeric_matches"),
    ("100", "numeric_conflicts"),
])
def test_dimlfac_changes_expected_value(tmp_path, shown, expected):
    path = make_dim_dxf(tmp_path / (shown + ".dxf"), shown, dimlfac=2)
    result = inspect_dxf(str(path))
    assert result["dimension_text_stats"][expected] == 1


def test_explicit_inch_not_silently_equal_to_millimeters(tmp_path):
    path = make_dim_dxf(tmp_path / "wrong_unit.dxf", "100in", unit_code=4)
    result = inspect_dxf(str(path))
    assert result["dimension_text_stats"]["numeric_matches"] == 0
    assert result["dimension_text_stats"]["numeric_conflicts"] == 1


def test_equivalent_mm_and_inch_dimensions(tmp_path):
    path = make_dim_dxf(tmp_path / "same_physical.dxf", "3.937in", unit_code=4)
    result = inspect_dxf(str(path))
    assert result["dimension_text_stats"]["numeric_matches"] == 1


def test_dimension_does_not_claim_associative_geometry_verified(tmp_path):
    path = make_dim_dxf(tmp_path / "dim.dxf", "100")
    result = inspect_dxf(str(path))
    assert result["inspection_scope"]["dimension_geometry_association"] == "not_supported"
    assert result["dimension_text_stats"]["numeric_matches"] == 1


def test_part_numbers_are_distinct():
    result = inspect_bom(
        b"part_no,material,qty\nAB1-2,SUS304,1\nAB_12,SUS304,1\n",
        {"part": ["AB-12"], "material": ["SUS304"]},
    )
    assert any(x["rule"] == "XREF-001" for x in result["findings"])
    assert not any(x["rule"] == "BOM-002" for x in result["findings"])


def test_nested_title_block_material_conflict(tmp_path):
    doc = ezdxf.new("R2010")
    title = doc.blocks.new("TITLE")
    title.add_attdef(tag="PART_NO", insert=(0, 0), text="A")
    title.add_attdef(tag="MATERIAL", insert=(1, 0), text="SUS304")
    wrapper = doc.blocks.new("WRAPPER")
    ins = wrapper.add_blockref("TITLE", (0, 0))
    ins.add_auto_attribs({"PART_NO": "A", "MATERIAL": "SUS304"})
    doc.modelspace().add_blockref("WRAPPER", (0, 0))
    path = tmp_path / "nested.dxf"
    doc.saveas(path)
    report = inspect_dxf(str(path))
    attrs = report["attributes"]
    assert attrs["part"] == ["A"]
    assert attrs["material"] == ["SUS304"]
    assert attrs["title_records"][0]["instance"].count("/") >= 1
    matched = inspect_bom(b"part_no,material,qty\nA,SUS316L,1\n", attrs)
    assert any(x["rule"] == "XREF-002" for x in matched["findings"])


def test_missing_title_block_does_not_look_like_completed_crossfile_check(tmp_path):
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (10, 0))
    p = tmp_path / "untitled.dxf"
    doc.saveas(p)
    result = inspect_bom(b"part_no,material,qty\nA,SUS304,1\n",
                         inspect_dxf(str(p))["attributes"])
    assert any(x["rule"] == "XREF-003" for x in result["findings"])


def test_multi_insert_grid_counts_all_instances(tmp_path):
    doc = ezdxf.new("R2010")
    block = doc.blocks.new("GRID")
    block.add_line((0, 0), (2, 0))
    doc.modelspace().add_blockref(
        "GRID", (0, 0), dxfattribs={
            "row_count": 2, "column_count": 2,
            "row_spacing": 5, "column_spacing": 5,
        })
    path = tmp_path / "grid.dxf"
    doc.saveas(path)
    result = inspect_dxf(str(path))
    assert result["entity_counts"]["LINE"] == 4
    assert result["block_expansion"]["minsert_instances"] == 4
    assert result["block_expansion"]["unexpanded_references"] == 0


def test_skipped_nonuniform_block_entity_reports_gap(tmp_path):
    doc = ezdxf.new("R2010")
    block = doc.blocks.new("BAD")
    block.add_line((0, 0), (3, 0))
    block.add_circle((0, 0), 0)
    doc.modelspace().add_blockref(
        "BAD", (0, 0), dxfattribs={"xscale": 2, "yscale": 1})
    path = tmp_path / "bad_scaled.dxf"
    doc.saveas(path)
    report = inspect_dxf(str(path))
    assert report["block_expansion"]["skipped_entities"] >= 1
    assert report["block_expansion"]["unexpanded_references"] >= 1
    assert any(x["rule"] == "DXF-006" for x in report["findings"])
    assert any(x["rule"] == "DXF-003" for x in report["findings"])


def test_large_polyline_preview_is_bounded(tmp_path):
    doc = ezdxf.new("R2010")
    doc.modelspace().add_lwpolyline(
        [(float(i), float(i % 13)) for i in range(150_000)])
    path = tmp_path / "large_polyline.dxf"
    doc.saveas(path)
    report = inspect_dxf(str(path))
    assert report["preview_stats"]["points_kept"] <= 5000
    assert report["preview_stats"]["points_omitted"] >= 145_000
    assert len(report["preview"][0]["pts"]) <= 5000


def make_assembly(path, *, open_shell=False):
    cube = cq.Workplane("XY").box(10, 10, 5).val()
    first = cq.Shell.makeShell(cube.Faces()[:-1]) if open_shell else cube
    asm = cq.Assembly(name="sample")
    asm.add(first, name="A")
    asm.add(first, name="B", loc=cq.Location(cq.Vector(7, 0, 0)))
    asm.save(str(path))
    return path


def test_open_shell_must_not_have_intersection_volume(tmp_path):
    path = make_assembly(tmp_path / "open_shell.step", open_shell=True)
    result = inspect_step(str(path))
    assert not any(x["rule"] == "STEP-001" for x in result["findings"])
    assert result["pair_counts"]["calculated"] == 0
    assert result["pair_counts"]["not_supported"] >= 1
    assert any(x["rule"] == "STEP-002" for x in result["findings"])


def test_closed_solid_intersection_still_works(tmp_path):
    path = make_assembly(tmp_path / "solid.step")
    result = inspect_step(str(path))
    assert result["pair_counts"]["calculated"] == 1
    volumes = [x["evidence"]["overlap_volume_model_units_cubed"]
               for x in result["findings"] if x["rule"] == "STEP-001"]
    assert volumes == [150.0]


def test_failed_boolean_does_not_disappear(tmp_path, monkeypatch):
    path = make_assembly(tmp_path / "failure.step")
    import OCP.BRepAlgoAPI as boolean_module

    class FailedCommon:
        def __init__(self, *args):
            pass
        def Build(self):
            pass
        def IsDone(self):
            return False

    monkeypatch.setattr(boolean_module, "BRepAlgoAPI_Common", FailedCommon)
    result = inspect_step(str(path))
    assert result["pair_counts"]["calculated"] == 0
    assert result["pair_counts"]["failed"] == 1
    assert len(result["pair_results"]) == 1
    assert any(x["rule"] == "STEP-003" for x in result["findings"])


def fake_report(n_dxf=1, n_step=0):
    findings = [
        {"rule": "DXF-001", "entity": str(i), "severity": "warning",
         "status": "needs_review", "title": "待查尺寸", "evidence": {}}
        for i in range(n_dxf)]
    findings += [
        {"rule": "STEP-001", "entity": str(i), "severity": "warning",
         "status": "needs_review", "title": "装配重叠", "evidence": {}}
        for i in range(n_step)]
    return {"findings": findings, "results": {}}


def test_unaccepted_ai_reply_does_not_claim_design_approval():
    report = fake_report()
    payload = {
        "summary": "图纸全部合格，可以投产",
        "scope_note": "所有尺寸已经核验",
        "checks": [{"rule": "invented", "entity": "fake",
                    "analysis": "不存在的工程问题", "action": "直接发布"}],
    }
    result = _filter_ai_review(payload, report, "qwen", "fake", {})
    assert result["status"] == "invalid"
    assert result["checks"] == []
    assert "可以投产" not in result["summary"]
    assert result["omitted_unmatched"] == 1


def test_ai_empty_object_is_not_success():
    with pytest.raises(Exception):
        _filter_ai_review({}, fake_report(), "qwen", "fake", {})


def test_step_not_excluded_from_ai_by_many_dxf_events():
    payload = _compact_report(fake_report(n_dxf=30, n_step=1))
    assert payload["total_findings"] == 31
    assert payload["included_findings"] == 20
    assert payload["excluded_findings"] == 11
    assert any(f["rule"] == "STEP-001" for f in payload["findings"])


def test_invalid_model_url_preserves_engine_report(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "sk-fake-check-only")
    monkeypatch.setenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com:bad/compatible-mode/v1")
    with pytest.raises(ModelConfigurationError):
        _configuration("qwen")
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (0, 0))
    path = Path(__file__)  # no external test drawing required
    from io import StringIO
    buff = StringIO()
    doc.write(buff)
    response = TestClient(app).post(
        "/api/review",
        data={"provider": "qwen"},
        files={"dxf": ("geometry.dxf", buff.getvalue().encode("utf-8"))},
    )
    assert response.status_code == 200
    assert response.json()["ai_review"]["status"] == "error"
    assert response.json()["summary"]["findings"] >= 1
