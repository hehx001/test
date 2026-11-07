"""Read-only, evidence-first mechanical drawing review. No design modification."""
from __future__ import annotations

import csv
import io
import math
import re
from pathlib import Path


def issue(rule, title, severity, status, source, entity, evidence, advice):
    return dict(rule=rule, title=title, severity=severity, status=status,
                source=source, entity=entity, evidence=evidence, advice=advice)


def norm(s):
    return re.sub(r"[\s\-_]", "", str(s or "")).upper()


FIELD_NAMES = {
    "part": {"PARTNO", "PARTNUMBER", "PARTNUM", "PARTID", "零件号", "物料编码", "图号"},
    "material": {"MATERIAL", "MAT", "材质", "材料", "材料牌号"},
    "revision": {"REV", "REVISION", "VERSION", "版本", "版本号"},
}


def extract_attributes(document):
    """Only explicit INSERT attribute values are treated as title-block evidence."""
    found = {}
    for layout in document.layouts:
        for ins in layout.query("INSERT"):
            for attr in ins.attribs:
                tag = norm(attr.dxf.tag)
                for field, aliases in FIELD_NAMES.items():
                    if tag in aliases and attr.dxf.text.strip():
                        found.setdefault(field, set()).add(attr.dxf.text.strip())
    return {key: sorted(values) for key, values in found.items()}


_DIM_VALUE_PATTERN = re.compile(
    r'^\s*(?:R|Ø|⌀|Φ|%%[cC])?\s*'
    r'(?P<value>\d+-\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)'
    r'\s*(?:"|in|mm)?\s*R?\s*$', re.IGNORECASE
)


def _parse_dimension_number(label: str):
    """Parse a conservative subset of numeric dimension overrides.

    Supports decimal, common fractions, mixed fractions, diameter/radius
    markers, and inch/mm suffixes. Returns None for compound notes/callouts.
    """
    m = _DIM_VALUE_PATTERN.fullmatch(label.strip())
    if not m:
        return None
    x = m.group("value")
    if "-" in x and "/" in x:
        whole, fraction = x.split("-", 1)
        a, b = fraction.split("/", 1)
        return float(whole) + float(a) / float(b) if float(b) else None
    if "/" in x:
        a, b = x.split("/", 1)
        return float(a) / float(b) if float(b) else None
    return float(x)


def inspect_dxf(path: str):
    import ezdxf
    from ezdxf import DXFError

    try:
        doc = ezdxf.readfile(path)
    except (OSError, DXFError, ValueError) as exc:
        raise ValueError("DXF文件无法正确解析") from exc
    findings, drawing, counts = [], [], {"DIMENSION": 0, "LINE": 0, "CIRCLE": 0}
    dim_stats = {"default_or_placeholder": 0, "numeric_matches": 0,
                 "numeric_conflicts": 0, "custom_unverified": 0}
    custom_samples = []
    all_types = {}
    for layout in doc.layouts:
        for ent in layout:
            kind = ent.dxftype()
            all_types[kind] = all_types.get(kind, 0) + 1
            handle = ent.dxf.get("handle", "")
            if kind in counts:
                counts[kind] += 1
            if kind == "DIMENSION":
                shown = str(ent.dxf.get("text", "") or "").strip()
                if not shown or "<>" in shown:
                    dim_stats["default_or_placeholder"] += 1
                else:
                    displayed = _parse_dimension_number(shown)
                    raw = None
                    try:
                        m = ent.get_measurement()
                        if isinstance(m, (int, float)) and math.isfinite(float(m)):
                            raw = float(m)
                    except (ValueError, TypeError, ArithmeticError):
                        pass
                    # Without explicit source dimensions/angle semantics and
                    # DIMLFAC style interpretation, a numeric discrepancy
                    # must be reviewed, not automatically condemned.
                    if displayed is not None and raw is not None:
                        if abs(displayed - raw) <= max(1e-4, abs(raw) * 1e-4):
                            dim_stats["numeric_matches"] += 1
                        else:
                            dim_stats["numeric_conflicts"] += 1
                            findings.append(issue("DXF-001", "尺寸文字与原始几何测量值不一致",
                                                  "warning", "needs_review", layout.name, handle,
                                                  {"shown": shown, "parsed_value": displayed,
                                                   "raw_measurement": round(raw, 6),
                                                   "note": "需再核查DIMLFAC、单位换算、标注类型与特殊公差要求"},
                                                  "核查原始几何、尺寸样式和人工修改原因。"))
                    else:
                        dim_stats["custom_unverified"] += 1
                        if len(custom_samples) < 5:
                            custom_samples.append({"entity": handle, "shown": shown})
                p = ent.dxf.get("defpoint", None)
                if p is not None:
                    drawing.append({"type": "POINT", "id": handle, "xy": [float(p.x), float(p.y)]})
            elif kind == "LINE":
                a, b = ent.dxf.start, ent.dxf.end
                if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) < 1e-9:
                    findings.append(issue("DXF-002", "零长度直线", "warning", "confirmed",
                                          layout.name, handle, {"start": [a.x, a.y], "end": [b.x, b.y]},
                                          "检查该图元是否为冗余图形。"))
                if len(drawing) < 3000:
                    drawing.append({"type": "LINE", "id": handle,
                                    "a": [a.x, a.y], "b": [b.x, b.y]})
            elif kind == "CIRCLE":
                c, r = ent.dxf.center, float(ent.dxf.radius)
                if r <= 0:
                    findings.append(issue("DXF-003", "圆的半径非正数", "warning", "confirmed",
                                          layout.name, handle, {"radius": r}, "检查异常几何图元。"))
                if len(drawing) < 3000 and r > 0:
                    drawing.append({"type": "CIRCLE", "id": handle, "c": [c.x, c.y], "r": r})
            elif kind == "LWPOLYLINE" and len(drawing) < 3000:
                pts = [[float(x), float(y)] for x, y in ent.get_points("xy")]
                if len(pts) > 1:
                    drawing.append({"type": "POLYLINE", "id": handle, "pts": pts,
                                    "closed": bool(ent.closed), "has_bulge": any(abs(p[4]) > 1e-10 for p in ent.get_points("xyseb"))})
    if dim_stats["custom_unverified"]:
        findings.append(issue("DXF-005", "部分特殊尺寸注释未完成数值校核", "info", "needs_review",
                              "DRAWING", "DIMENSION", {"count": dim_stats["custom_unverified"],
                              "samples": custom_samples}, "特殊尺寸文本需结合原始设计要求复核。"))
    units = int(doc.header.get("$INSUNITS", 0))
    if units == 0:
        findings.append(issue("DXF-004", "文件未声明插入单位", "info", "needs_review", "HEADER", "$INSUNITS",
                              {"INSUNITS": 0}, "确认图纸设计单位和外部参照比例。"))
    attrs = extract_attributes(doc)
    for key, values in attrs.items():
        if len(values) > 1:
            findings.append(issue("DOC-001", "图框属性存在多个不同取值", "warning", "needs_review", "TITLE", key,
                                  {"field": key, "values": values}, "核对是否包含多个图框或多个版本。"))
    return {"filename": Path(path).name, "attributes": attrs, "units": units,
            "entity_counts": counts, "dimension_text_stats": dim_stats,
            "all_entity_types": all_types,
            "unrendered_entity_types": {k: v for k, v in all_types.items()
                                        if k not in {"LINE", "CIRCLE", "LWPOLYLINE"}},
            "preview": drawing, "findings": findings}


def inspect_bom(raw: bytes, drawing_attrs: dict):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("BOM编码不受支持") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("BOM为空或缺少列标题")
    colmap = {}
    for name in rows[0].keys():
        for field, aliases in FIELD_NAMES.items():
            if norm(name) in aliases:
                colmap[field] = name
        if norm(name) in {"QTY", "QUANTITY", "数量", "用量"}:
            colmap["qty"] = name
    if "part" not in colmap:
        raise ValueError("BOM缺少零件号列（例如part_no或图号）")
    findings, parts = [], {}
    for index, row in enumerate(rows, 2):
        part = (row.get(colmap["part"]) or "").strip()
        if not part:
            findings.append(issue("BOM-001", "BOM缺失零件号", "warning", "confirmed",
                                  "BOM", f"row:{index}", {"row": index}, "补全零件号。"))
            continue
        key = norm(part)
        if key in parts:
            findings.append(issue("BOM-002", "BOM零件号重复", "warning", "needs_review", "BOM", f"row:{index}",
                                  {"part_no": part, "first_row": parts[key][0]}, "核查是否为合法的分行明细。"))
        else:
            parts[key] = (index, row)
        if "qty" in colmap:
            q = (row.get(colmap["qty"]) or "").strip()
            try:
                if float(q) <= 0 or not math.isfinite(float(q)):
                    raise ValueError()
            except ValueError:
                findings.append(issue("BOM-003", "BOM数量无效", "warning", "confirmed", "BOM", f"row:{index}",
                                      {"part_no": part, "qty": q}, "核查物料数量。"))
    dparts = drawing_attrs.get("part", [])
    if len(dparts) == 1:
        key = norm(dparts[0])
        if key not in parts:
            findings.append(issue("XREF-001", "图纸零件号未在BOM中找到", "warning", "needs_review",
                                  "DRAWING+BOM", "part", {"drawing_part": dparts[0]},
                                  "核对图纸与BOM的发布版本及所属装配体。"))
        elif "material" in colmap and len(drawing_attrs.get("material", [])) == 1:
            rownum, row = parts[key]
            a = drawing_attrs["material"][0]
            b = (row.get(colmap["material"]) or "").strip()
            if b and norm(a) != norm(b):
                findings.append(issue("XREF-002", "图纸与BOM材料名称不一致", "warning", "confirmed",
                                      "DRAWING+BOM", f"row:{rownum}", {"drawing": a, "bom": b, "part_no": dparts[0]},
                                      "核实材料牌号及变更记录；名称不一致不必然代表材料技术上不等效。"))
    return {"row_count": len(rows), "columns": list(colmap.keys()), "findings": findings}


def inspect_step(path: str, max_parts: int = 80, max_pairs: int = 2000):
    """Check static intersections of assembly components; overlaps are candidates, not defects."""
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.TDF import TDF_LabelSequence, TDF_Label
        from OCP.TDataStd import TDataStd_Name
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
        from OCP.GProp import GProp_GProps
        from OCP.BRepGProp import BRepGProp
        from OCP.TopLoc import TopLoc_Location
    except ImportError as exc:
        raise ValueError("未安装cadquery-ocp，无法检查STEP") from exc
    rdr = STEPCAFControl_Reader()
    if "RetDone" not in str(rdr.ReadFile(path)):
        raise ValueError("STEP文件无法读取")
    doc = TDocStd_Document(TCollection_ExtendedString("audit"))
    if not rdr.Transfer(doc):
        raise ValueError("STEP几何转换失败")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = TDF_LabelSequence()
    tool.GetFreeShapes(free)
    parts = []

    def name(label):
        a = TDataStd_Name()
        return str(a.Get().ToExtString()) if label.FindAttribute(TDataStd_Name.GetID_s(), a) else "unnamed"

    def visit(label, trail, depth, parent_location):
        if depth > 15 or len(parts) > max_parts:
            raise ValueError("STEP装配层次过深或零件数超出安全限制")
        referred = TDF_Label()
        identity = tool.GetReferredShape_s(label, referred)
        real_label = referred if identity else label
        location = parent_location.Multiplied(tool.GetLocation_s(label))
        children = TDF_LabelSequence()
        if tool.IsAssembly_s(real_label) and tool.GetComponents_s(real_label, children, False):
            for index in range(1, children.Length() + 1):
                child = children.Value(index)
                ref = TDF_Label()
                child_label = ref if tool.GetReferredShape_s(child, ref) else child
                child_name = name(child)
                if child_name.isdigit() or child_name == "unnamed":
                    child_name = name(child_label)
                visit(child, trail + "/" + child_name, depth + 1, location)
            return
        shape = tool.GetShape_s(real_label)
        if not shape.IsNull():
            shape = shape.Moved(location)
            box = Bnd_Box()
            BRepBndLib.Add_s(shape, box)
            parts.append({"name": name(label), "path": trail, "shape": shape, "bbox": box})

    for i in range(1, free.Length() + 1):
        visit(free.Value(i), name(free.Value(i)), 0, TopLoc_Location())
    if len(parts) > max_parts:
        raise ValueError("STEP零件数超出安全限制")
    findings, checked = [], 0
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            if a["bbox"].IsOut(b["bbox"]):
                continue
            checked += 1
            if checked > max_pairs:
                raise ValueError("STEP潜在相交零件对超出安全限制")
            common = BRepAlgoAPI_Common(a["shape"], b["shape"])
            common.Build()
            if not common.IsDone():
                continue
            mass = GProp_GProps()
            BRepGProp.VolumeProperties_s(common.Shape(), mass)
            overlap = float(mass.Mass())
            if overlap > 1e-6:
                findings.append(issue("STEP-001", "装配零件存在实体交集", "warning", "needs_review", "STEP",
                                      f"{i}:{j}", {"part_a": a["path"], "part_b": b["path"],
                                                    "overlap_volume_model_units_cubed": round(overlap, 6)},
                                      "核对配合类型、预期过盈及设计模型简化情况。"))
    return {"part_count": len(parts), "candidate_pairs": checked, "total_pairs": len(parts)*(len(parts)-1)//2,
            "parts": [{"name": p["name"], "path": p["path"]} for p in parts], "findings": findings}
