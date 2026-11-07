"""Create deliberately inconsistent demo inputs. Requires ezdxf and cadquery."""
from pathlib import Path
import ezdxf
import cadquery as cq

folder = Path(__file__).resolve().parent

doc = ezdxf.new(setup=True)
m = doc.modelspace()
m.add_line((0, 0), (0, 0))
m.add_line((0, 0), (70, 0))
m.add_line((70, 0), (70, 40))
m.add_line((70, 40), (0, 40))
m.add_line((0, 40), (0, 0))
d = m.add_linear_dim(base=(0, -12), p1=(0, 0), p2=(70, 0), text="72")
d.render()
b = doc.blocks.new("TITLE")
b.add_attdef(tag="PART_NO", insert=(0, 0), text="P-001")
b.add_attdef(tag="MATERIAL", insert=(0, 0), text="SUS304")
ref = m.add_blockref("TITLE", (0, 0))
ref.add_auto_attribs({"PART_NO": "P-001", "MATERIAL": "SUS304"})
doc.saveas(folder / "demo_part.dxf")
(folder / "demo_bom.csv").write_text("part_no,material,qty\nP-001,316L,1\n", encoding="utf-8")
asm = cq.Assembly(name="demo")
asm.add(cq.Workplane("XY").box(30, 20, 10), name="plate_A")
asm.add(cq.Workplane("XY").box(30, 20, 10).translate((25, 0, 0)), name="plate_B")
asm.save(str(folder / "demo_assembly.step"))
print("Created demo_part.dxf, demo_bom.csv, demo_assembly.step")
