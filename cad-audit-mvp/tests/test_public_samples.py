"""Reproducible integration tests using independently authored public CAD files.

Files are downloaded on demand, at immutable revisions, and checked against their
original Git blob SHA. They are not redistributed by this repository.
Run: pytest -s -q tests/test_public_samples.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

import ezdxf
import pytest

from cad_audit.engine import inspect_dxf, inspect_step


SAMPLES = {
    "mounting_plate": (
        "haiha-ux/cad-sample-tests",
        "150f4dce05a41a85d956189de8ded2cdd0d4a4d3",
        "bai_a/CST-A-001_mounting_plate.dxf",
        "f2e34c19c3503ac98febf2d2dfffe2711dab8bae",
    ),
    "step_bearing": (
        "haiha-ux/cad-sample-tests",
        "150f4dce05a41a85d956189de8ded2cdd0d4a4d3",
        "bai_c/CST-C-001_step_bearing.dxf",
        "17f14f8ab370fd5b9913bf7a22409866a360675a",
    ),
    "cadquery_dxf": (
        "CadQuery/cadquery",
        "477d87d738c57f46e8e073d2b958ee0a32d29258",
        "tests/testdata/genshi.dxf",
        "9662e95ec1e9a4d178073d0953a5d96919188df5",
    ),
    "cadquery_step": (
        "CadQuery/cadquery",
        "477d87d738c57f46e8e073d2b958ee0a32d29258",
        "tests/testdata/red_cube_blue_cylinder.step",
        "4ed12421d740787953eeee57e717d7f553473ed6",
    ),
    "freecad_step": (
        "FreeCAD/FreeCAD",
        "5ce3b8804dd91cacbcc9e740d14b55e062536f3b",
        "data/tests/Step/as1-ac-214_small.stp",
        "85b358f640a9dd7f4c1d4f18e0a9ab87c6c1fafa",
    ),
}


def download_sample(key: str, folder: Path) -> Path:
    repo, revision, src, expected_blob_sha = SAMPLES[key]
    url = f"https://raw.githubusercontent.com/{repo}/{revision}/{src}"
    req = Request(url, headers={"User-Agent": "Mechanical-CAD-Audit-Test/0.1"})
    with urlopen(req, timeout=45) as response:
        payload = response.read(1_000_001)
    assert len(payload) <= 1_000_000, f"oversize sample: {key}"
    git_blob = hashlib.sha1(b"blob " + str(len(payload)).encode("ascii") +
                            b"\0" + payload).hexdigest()
    assert git_blob == expected_blob_sha, f"source changed or invalid: {key}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(src).name
    path.write_bytes(payload)
    return path


def result_record(key, path, result):
    details = {
        "id": key,
        "source": SAMPLES[key][0] + "/" + SAMPLES[key][2],
        "source_revision": SAMPLES[key][1],
        "git_blob_sha": SAMPLES[key][3],
        "file_bytes": path.stat().st_size,
        "rule_counts": {},
        "status_counts": {},
        "issues": [],
    }
    if "entity_counts" in result:
        details["entity_counts"] = result["entity_counts"]
        details["declared_units"] = result["units"]
        details["dimension_text_stats"] = result["dimension_text_stats"]
        details["all_entity_types"] = result["all_entity_types"]
        details["unrendered_entity_types"] = result["unrendered_entity_types"]
    if "part_count" in result:
        details["part_count"] = result["part_count"]
        details["candidate_pairs"] = result["candidate_pairs"]
    for problem in result["findings"]:
        rule = problem["rule"]
        status = problem["status"]
        details["rule_counts"][rule] = details["rule_counts"].get(rule, 0) + 1
        details["status_counts"][status] = details["status_counts"].get(status, 0) + 1
        details["issues"].append({
            "rule": rule, "status": status,
            "entity": problem["entity"], "title": problem["title"]
        })
    return details


@pytest.fixture(scope="module")
def public_data(tmp_path_factory):
    folder = tmp_path_factory.mktemp("public_cad")
    return {name: download_sample(name, folder) for name in SAMPLES}


def test_authentic_technical_drawings(public_data):
    expected_dimension_counts = {"mounting_plate": 15, "step_bearing": 22}
    for key in ("mounting_plate", "step_bearing", "cadquery_dxf"):
        path = public_data[key]
        doc = ezdxf.readfile(path)
        audit = doc.audit()
        assert not audit.errors, f"DXF audit error for {key}: {audit.errors}"
        result = inspect_dxf(str(path))
        assert result["filename"] == path.name
        if key in expected_dimension_counts:
            assert result["entity_counts"]["DIMENSION"] == expected_dimension_counts[key]
            assert result["dimension_text_stats"]["numeric_conflicts"] == 0
        if key == "mounting_plate":
            assert not any(x["rule"] == "DXF-001" for x in result["findings"])
        if key == "step_bearing":
            assert result["dimension_text_stats"]["numeric_matches"] >= 21
            assert result["dimension_text_stats"]["custom_unverified"] <= 1
        if key == "cadquery_dxf":
            assert result["all_entity_types"].get("SPLINE") == 12
            assert result["unrendered_entity_types"].get("SPLINE") == 12
        print("REAL_DXF " + json.dumps(result_record(key, path, result), ensure_ascii=False))


def test_authentic_step_files(public_data):
    for key in ("cadquery_step", "freecad_step"):
        path = public_data[key]
        result = inspect_step(str(path))
        assert result["part_count"] >= 1, f"no STEP parts: {key}"
        print("REAL_STEP " + json.dumps(result_record(key, path, result), ensure_ascii=False))


def test_controlled_dimension_mutation_on_authentic_drawing(public_data, tmp_path):
    """Change one authentic dimension label, then check its original entity handle."""
    src = public_data["mounting_plate"]
    doc = ezdxf.readfile(src)
    target = next(iter(doc.modelspace().query("DIMENSION")))
    handle = target.dxf.handle
    target.dxf.text = "9999.99"
    changed = tmp_path / "changed_mounting_plate.dxf"
    doc.saveas(changed)
    findings = inspect_dxf(str(changed))["findings"]
    matches = [x for x in findings if x["rule"] == "DXF-001" and x["entity"] == handle]
    assert matches and matches[0]["evidence"]["shown"] == "9999.99"
    print("MUTATION " + json.dumps({
        "source": str(src.name), "altered_entity": handle,
        "rule_detected": matches[0]["rule"], "status": matches[0]["status"]
    }, ensure_ascii=False))
