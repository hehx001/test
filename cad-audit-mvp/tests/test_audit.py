import io
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
from cad_audit.api import app
from cad_audit.engine import inspect_dxf, inspect_bom, inspect_step


def sample_dxf(p):
    doc = ezdxf.new(setup=True)
    m = doc.modelspace()
    m.add_line((0, 0), (0, 0))
    dim = m.add_linear_dim(base=(0, 10), p1=(0, 0), p2=(70, 0), text="72")
    dim.render()
    blk = doc.blocks.new("TITLE")
    blk.add_attdef(tag="PART_NO", insert=(0, 0), text="P-001")
    blk.add_attdef(tag="MATERIAL", insert=(0, 0), text="SUS304")
    ins = m.add_blockref("TITLE", (0, 0))
    ins.add_auto_attribs({"PART_NO": "P-001", "MATERIAL": "SUS304"})
    doc.saveas(p)


def test_dimensions_and_bom(tmp_path):
    path = tmp_path / "part.dxf"
    sample_dxf(path)
    res = inspect_dxf(str(path))
    assert {f["rule"] for f in res["findings"]} >= {"DXF-001", "DXF-002"}
    assert res["attributes"]["part"] == ["P-001"]
    bom = "part_no,material,qty\nP-001,316L,2\n".encode()
    x = inspect_bom(bom, res["attributes"])
    assert any(f["rule"] == "XREF-002" for f in x["findings"])


def test_bom_invalid_quantity():
    x = inspect_bom("图号,数量\nA,0\n".encode(), {})
    assert x["findings"][0]["rule"] == "BOM-003"


def test_endpoint(tmp_path):
    path = tmp_path / "part.dxf"
    sample_dxf(path)
    client = TestClient(app)
    r = client.post("/api/review", files={"dxf": ("part.dxf", path.read_bytes()),
                                            "bom": ("bom.csv", b"part_no,material,qty\nP-001,316L,2\n")})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["confirmed"] >= 2
    assert client.post("/api/review").status_code == 400
    assert client.post("/api/review", files={"dxf": ("unsafe.txt", b"x")}).status_code == 400


def test_step_overlap(tmp_path):
    import cadquery as cq
    asm = cq.Assembly(name="demo")
    asm.add(cq.Workplane().box(10, 10, 5), name="housing")
    asm.add(cq.Workplane().box(10, 10, 5).translate((7, 0, 0)), name="cover")
    p = tmp_path / "assembly.step"
    asm.save(str(p))
    x = inspect_step(str(p))
    assert x["part_count"] == 2
    assert x["findings"][0]["rule"] == "STEP-001"
    assert abs(x["findings"][0]["evidence"]["overlap_volume_model_units_cubed"] - 150) < 0.01


def test_nested_step_placement(tmp_path):
    """A component inside a translated subassembly must inherit parent placement."""
    import cadquery as cq
    nested = cq.Assembly(name="nested")
    nested.add(cq.Workplane().box(5, 5, 5), name="inner")
    top = cq.Assembly(name="root")
    top.add(cq.Workplane().box(5, 5, 5), name="outer", loc=cq.Location(cq.Vector(100, 0, 0)))
    top.add(nested, name="sub", loc=cq.Location(cq.Vector(100, 0, 0)))
    path = tmp_path / "nested.step"
    top.save(str(path))
    r = inspect_step(str(path))
    assert len(r["findings"]) == 1
    assert abs(r["findings"][0]["evidence"]["overlap_volume_model_units_cubed"] - 125) < .01
