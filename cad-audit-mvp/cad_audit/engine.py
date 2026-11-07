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


from .dimension_rules import check_dimension
from .title_blocks import FIELD_NAMES, extract_attributes, field_key, part_key, material_key


def norm(s):
    """Legacy field-tag normalization; NEVER use to identify a part."""
    return field_key(s)


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
    unit_code = int(doc.header.get("$INSUNITS", 0))
    all_types = {}
    preview_points_used = 0
    preview_points_skipped = 0
    PREVIEW_POINTS_MAX = 5000
    blocks = {"references": 0, "virtual_entities": 0, "empty_references": 0,
              "unexpanded_references": 0, "skipped_entities": 0,
              "minsert_instances": 0, "examples": []}

    def visible_entities(layout):
        # Never explode() a design file: virtual_entities() retains the source.
        # Trace paths preserve the referring INSERT's original DXF handle.
        def visit(ent, path, depth):
            yield ent, path
            if ent.dxftype() != "INSERT":
                return
            blocks["references"] += 1
            if depth >= 8 or blocks["virtual_entities"] >= 15000:
                blocks["unexpanded_references"] += 1
                if len(blocks["examples"]) < 8:
                    blocks["examples"].append({"entity": path, "reason": "depth_or_entity_cap"})
                return
            if ent.mcount > 1:
                try:
                    for idx, instance in enumerate(ent.multi_insert()):
                        blocks["minsert_instances"] += 1
                        if blocks["minsert_instances"] > 15000:
                            raise ValueError("multi_insert_cap")
                        yield from visit(instance, path + f"@{idx}", depth + 1)
                except Exception as exc:
                    blocks["unexpanded_references"] += 1
                    if len(blocks["examples"]) < 8:
                        blocks["examples"].append({"entity": path, "reason": type(exc).__name__})
                return

            def skipped(source_entity, reason):
                blocks["skipped_entities"] += 1
                blocks["unexpanded_references"] += 1
                kind = source_entity.dxftype()
                source_handle = str(source_entity.dxf.get("handle", ""))
                if len(blocks["examples"]) < 8:
                    blocks["examples"].append({"entity": path, "source_handle": source_handle,
                                               "kind": kind, "reason": str(reason)[:200]})
                if kind == "CIRCLE":
                    radius = float(source_entity.dxf.get("radius", 0))
                    if radius <= 0:
                        findings.append(issue("DXF-003", "图块中的圆半径非正数",
                                              "warning", "confirmed", layout.name,
                                              path + "/" + source_handle,
                                              {"radius": radius, "note": str(reason)[:120]},
                                              "检查源图块内异常圆及图块变换。"))
            try:
                children_seen = False
                before_skips = blocks["skipped_entities"]
                for index, child in enumerate(
                    ent.virtual_entities(skipped_entity_callback=skipped)
                ):
                    children_seen = True
                    if blocks["virtual_entities"] >= 15000:
                        blocks["unexpanded_references"] += 1
                        if len(blocks["examples"]) < 8:
                            blocks["examples"].append({"entity": path, "reason": "entity_cap"})
                        break
                    blocks["virtual_entities"] += 1
                    yield from visit(child, path + "/" + str(index), depth + 1)
                if not children_seen and blocks["skipped_entities"] == before_skips:
                    block_name = str(ent.dxf.get("name", ""))
                    definition = doc.blocks.get(block_name)
                    if definition is not None and not any(True for _ in definition):
                        blocks["empty_references"] += 1
                    else:
                        blocks["unexpanded_references"] += 1
                        if len(blocks["examples"]) < 8:
                            blocks["examples"].append({
                                "entity": path, "reason": "missing_or_unsupported_block"})
            except Exception as exc:
                blocks["unexpanded_references"] += 1
                if len(blocks["examples"]) < 8:
                    blocks["examples"].append({"entity": path, "reason": type(exc).__name__})

        for entity in layout:
            yield from visit(entity, str(entity.dxf.get("handle", "")), 0)

    for layout in doc.layouts:
        for ent, handle in visible_entities(layout):
            kind = ent.dxftype()
            all_types[kind] = all_types.get(kind, 0) + 1
            if kind in counts:
                counts[kind] += 1
            if kind == "DIMENSION":
                classification, evidence = check_dimension(ent, unit_code, layout.name)
                dim_stats[classification] += 1
                if classification == "numeric_conflicts":
                    findings.append(issue("DXF-001", "尺寸显示值与实体定义测量结果不一致",
                                          "warning", "needs_review", layout.name, handle,
                                          evidence, "复核实际尺寸对象、有效尺寸样式与原始几何关联。"))
                elif classification == "custom_unverified" and len(custom_samples) < 12:
                    custom_samples.append({"entity": handle, **evidence})
                p = ent.dxf.get("defpoint", None)
                if p is not None and preview_points_used < PREVIEW_POINTS_MAX:
                    drawing.append({"type": "POINT", "id": handle, "xy": [float(p.x), float(p.y)]})
                    preview_points_used += 1
            elif kind == "LINE":
                a, b = ent.dxf.start, ent.dxf.end
                if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) < 1e-9:
                    findings.append(issue("DXF-002", "零长度直线", "warning", "confirmed",
                                          layout.name, handle, {"start": [a.x, a.y], "end": [b.x, b.y]},
                                          "检查该图元是否为冗余图形。"))
                if len(drawing) < 3000 and preview_points_used + 2 <= PREVIEW_POINTS_MAX:
                    drawing.append({"type": "LINE", "id": handle,
                                    "a": [a.x, a.y], "b": [b.x, b.y]})
                    preview_points_used += 2
            elif kind == "CIRCLE":
                c, r = ent.dxf.center, float(ent.dxf.radius)
                if r <= 0:
                    findings.append(issue("DXF-003", "圆的半径非正数", "warning", "confirmed",
                                          layout.name, handle, {"radius": r}, "检查异常几何图元。"))
                if len(drawing) < 3000 and r > 0 and preview_points_used + 2 <= PREVIEW_POINTS_MAX:
                    drawing.append({"type": "CIRCLE", "id": handle, "c": [c.x, c.y], "r": r})
                    preview_points_used += 2
            elif kind == "LWPOLYLINE":
                original = [[float(x), float(y)] for x, y in ent.get_points("xy")]
                budget = PREVIEW_POINTS_MAX - preview_points_used
                if len(original) > 1 and budget >= 2 and len(drawing) < 3000:
                    step = max(1, math.ceil((len(original) - 1) / (budget - 1)))
                    indices = list(range(0, len(original) - 1, step)) + [len(original) - 1]
                    pts = [original[i] for i in indices]
                    preview_points_skipped += len(original) - len(pts)
                    preview_points_used += len(pts)
                    drawing.append({"type": "POLYLINE", "id": handle, "pts": pts,
                                    "closed": bool(ent.closed),
                                    "has_bulge": any(abs(p[4]) > 1e-10
                                                     for p in ent.get_points("xyseb")),
                                    "preview_simplified": len(pts) < len(original)})
                elif len(original) > 1:
                    preview_points_skipped += len(original)
    if blocks["unexpanded_references"]:
        findings.append(issue("DXF-006", "部分图块未成功展开，检查范围不完整",
                              "warning", "needs_review", "DRAWING", "INSERT",
                              {"unexpanded": blocks["unexpanded_references"],
                               "examples": blocks["examples"]},
                              "检查图块引用、外部参照与CAD软件原图。"))
    if dim_stats["custom_unverified"]:
        findings.append(issue("DXF-005", "部分特殊尺寸注释未完成数值校核", "info", "needs_review",
                              "DRAWING", "DIMENSION", {"count": dim_stats["custom_unverified"],
                              "samples": custom_samples}, "特殊尺寸文本需结合原始设计要求复核。"))
    units = unit_code
    if units == 0:
        findings.append(issue("DXF-004", "文件未声明插入单位", "info", "needs_review", "HEADER", "$INSUNITS",
                              {"INSUNITS": 0}, "确认图纸设计单位和外部参照比例。"))
    attrs = extract_attributes(doc)
    if attrs.get("title_scan_warnings"):
        findings.append(issue("DOC-002", "部分图签或嵌套属性未能可靠取得",
                              "warning", "needs_review", "TITLE", "INSERT",
                              {"examples": attrs["title_scan_warnings"][:10],
                               "count": len(attrs["title_scan_warnings"])},
                              "在原生CAD中核实图签实例和所依赖的图块。"))
    for key in ("part", "material", "revision"):
        values = attrs.get(key, [])
        if len(values) > 1:
            findings.append(issue("DOC-001", "图框属性存在多个不同取值", "warning", "needs_review", "TITLE", key,
                                  {"field": key, "values": values}, "核对是否包含多个图框或多个版本。"))
    return {"filename": Path(path).name, "attributes": attrs, "units": units,
            "inspection_scope": {"dimension_text": "partial",
                "dimension_geometry_association": "not_supported",
                "drawn_geometry": ("partial" if blocks["unexpanded_references"] else "checked_supported_types"),
                "preview": "illustrative_only"},
            "entity_counts": counts, "dimension_text_stats": dim_stats,
            "all_entity_types": all_types, "block_expansion": blocks,
            "unrendered_entity_types": {k: v for k, v in all_types.items()
                                        if k not in {"LINE", "CIRCLE", "LWPOLYLINE", "INSERT"}},
            "preview": drawing,
            "preview_stats": {"point_limit": PREVIEW_POINTS_MAX,
                              "points_kept": preview_points_used,
                              "points_omitted": preview_points_skipped,
                              "simplified": preview_points_skipped > 0},
            "findings": findings}


def inspect_bom(raw: bytes, drawing_attrs: dict):
    """Compare released BOM rows and individual title instances by exact part ID."""
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
            if field_key(name) in aliases:
                colmap[field] = name
        if field_key(name) in {"QTY", "QUANTITY", "数量", "用量"}:
            colmap["qty"] = name
    if "part" not in colmap:
        raise ValueError("BOM缺少零件号列（例如part_no或图号）")

    findings, parts = [], {}
    for index, row in enumerate(rows, 2):
        part = part_key(row.get(colmap["part"]))
        if not part:
            findings.append(issue("BOM-001", "BOM缺失零件号", "warning", "confirmed",
                                  "BOM", f"row:{index}", {"row": index}, "补全零件号。"))
            continue
        key = part_key(part)  # don't erase hyphens, underscores, or embedded spaces
        if key in parts:
            findings.append(issue("BOM-002", "BOM零件号重复", "warning", "needs_review",
                                  "BOM", f"row:{index}",
                                  {"part_no": part, "first_row": parts[key][0]},
                                  "核查是否为合法的分行明细。"))
        else:
            parts[key] = (index, row)
        if "qty" in colmap:
            q = (row.get(colmap["qty"]) or "").strip()
            try:
                if float(q) <= 0 or not math.isfinite(float(q)):
                    raise ValueError()
            except ValueError:
                findings.append(issue("BOM-003", "BOM数量无效", "warning", "confirmed",
                                      "BOM", f"row:{index}",
                                      {"part_no": part, "qty": q}, "核查物料数量。"))
    records = drawing_attrs.get("title_records")
    if records is None:
        records = []
        # Compatibility with callers that supply one already verified title.
        dparts = drawing_attrs.get("part", [])
        if len(dparts) == 1:
            rec = {"part": dparts[0], "instance": "legacy-single-title"}
            for key in ("material", "revision"):
                vals = drawing_attrs.get(key, [])
                if len(vals) == 1:
                    rec[key] = vals[0]
            records.append(rec)
    if drawing_attrs.get("_dxf_attached") and not records:
        findings.append(issue("XREF-003", "没有可靠图签，跨文件核查未完成",
                              "info", "needs_review", "DRAWING+BOM", "TITLE",
                              {"required": ["part", "material", "revision"]},
                              "检查原始图签与嵌套图块，补充有效编号后重新比对。"))
    for record in records:
        path = str(record.get("instance", "TITLE"))
        part = part_key(record.get("part"))
        if not part:
            findings.append(issue("XREF-003", "图签缺少可核实零件编号",
                                  "info", "needs_review", "DRAWING+BOM", path,
                                  {"title_record": record},
                                  "本图签的BOM关联检查未完成；补齐零件编号。"))
            continue
        match = parts.get(part)
        if match is None:
            findings.append(issue("XREF-001", "图纸零件号未在BOM中找到",
                                  "warning", "needs_review", "DRAWING+BOM", path,
                                  {"drawing_part": part, "title_instance": path},
                                  "核对发布版图纸与BOM的零件编号，保留内部连字符和下划线。"))
            continue
        row_no, row = match
        if "material" in colmap:
            drawing_material = str(record.get("material", "")).strip()
            bom_material = str(row.get(colmap["material"]) or "").strip()
            if not drawing_material or not bom_material:
                findings.append(issue("XREF-004", "材料信息缺失，材料核查未完成",
                                      "info", "needs_review", "DRAWING+BOM", path,
                                      {"part_no": part, "drawing": drawing_material,
                                       "bom": bom_material, "bom_row": row_no},
                                      "核实图纸与BOM的材料字段。"))
            elif material_key(drawing_material) != material_key(bom_material):
                findings.append(issue("XREF-002", "图纸与BOM材料名称不一致",
                                      "warning", "confirmed", "DRAWING+BOM", path,
                                      {"part_no": part, "drawing": drawing_material,
                                       "bom": bom_material, "bom_row": row_no},
                                      "复核材料牌号与变更记录；名称差异不直接等于材料不等效。"))
        if "revision" in colmap:
            drawing_rev = str(record.get("revision", "")).strip()
            bom_rev = str(row.get(colmap["revision"]) or "").strip()
            if not drawing_rev or not bom_rev:
                findings.append(issue("XREF-005", "版本信息缺失，核查未完成",
                                      "info", "needs_review", "DRAWING+BOM", path,
                                      {"part_no": part, "bom_row": row_no},
                                      "补充图纸与BOM版本后执行一致性检查。"))
            elif drawing_rev != bom_rev:
                findings.append(issue("XREF-006", "图纸与BOM版本不一致",
                                      "warning", "needs_review", "DRAWING+BOM", path,
                                      {"part_no": part, "drawing": drawing_rev,
                                       "bom": bom_rev, "bom_row": row_no},
                                      "核实已批准的正式设计版本。"))
    missing_columns = [name for name in ("material", "revision") if name not in colmap]
    if records and missing_columns:
        findings.append(issue("XREF-007", "BOM缺少关键字段，跨文件核查范围不完整",
                              "info", "not_supported", "DRAWING+BOM", "BOM_COLUMNS",
                              {"missing_columns": missing_columns},
                              "补充项目要求核查的材料和版本字段。"))
    checked = bool(records) and all(
        part_key(rec.get("part")) and part_key(rec.get("part")) in parts and
        all(str(rec.get(field, "")).strip() and
            str(parts[part_key(rec["part"])][1].get(colmap[field]) or "").strip()
            for field in ("material", "revision") if field in colmap)
        for rec in records
    )
    return {"row_count": len(rows), "columns": list(colmap.keys()),
            "title_instances_checked": len(records),
            "crossfile_check_complete": bool(checked and not missing_columns
                                            and not drawing_attrs.get("title_scan_warnings")),
            "missing_columns": missing_columns,
            "findings": findings}


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
        from OCP.TopLoc import TopLoc_Location
        from .step_safety import solids_and_validity, intersection_volume
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
            solids, shape_status = solids_and_validity(shape)
            parts.append({"name": name(label), "path": trail, "shape": shape,
                          "bbox": box, "geometry": shape_status})

    for i in range(1, free.Length() + 1):
        visit(free.Value(i), name(free.Value(i)), 0, TopLoc_Location())
    if len(parts) > max_parts:
        raise ValueError("STEP零件数超出安全限制")
    findings, checked = [], 0
    success, failed, unsupported = 0, 0, 0
    pair_results = []
    total_pairs = len(parts) * (len(parts) - 1) // 2
    for index, part in enumerate(parts):
        if not part["geometry"]["usable"]:
            findings.append(issue("STEP-002", "该对象不是已验证的封闭实体，体积干涉检查不适用",
                                  "info", "not_supported", "STEP", str(index),
                                  {"part": part["path"], "geometry": part["geometry"]},
                                  "应提供有效封闭实体或选择适用于曲面模型的检查任务。"))

    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            if a["bbox"].IsOut(b["bbox"]):
                continue
            checked += 1
            pair_id = f"{i}:{j}"
            evidence = {"part_a": a["path"], "part_b": b["path"]}
            status = "success"
            if checked > max_pairs:
                status, detail = "not_supported", "candidate_pair_limit"
            elif not a["geometry"]["usable"] or not b["geometry"]["usable"]:
                status, detail = "not_supported", "non_solid_or_invalid_component"
            else:
                try:
                    common = BRepAlgoAPI_Common(a["shape"], b["shape"])
                    common.Build()
                    if not common.IsDone():
                        status, detail = "failed", "boolean_algorithm_not_done"
                    else:
                        overlap, detail = intersection_volume(common.Shape())
                except Exception as exc:
                    status, detail = "failed", type(exc).__name__
            if status == "success":
                success += 1
                if overlap > 1e-6:
                    findings.append(issue("STEP-001", "装配零件存在已计算的实体交集",
                                          "warning", "needs_review", "STEP", pair_id,
                                          {**evidence,
                                           "overlap_volume_model_units_cubed": round(overlap, 6),
                                           "length_unit": "not_verified",
                                           "algorithm_zero_volume_threshold": 1e-6},
                                          "核对配合类型、预期过盈及设计模型简化情况。"))
            else:
                if status == "failed":
                    failed += 1
                    rule = "STEP-003"
                    title = "实体布尔运算失败，不能据此判定无干涉"
                else:
                    unsupported += 1
                    rule = "STEP-004"
                    title = "该零件配对的体积检查未执行"
                findings.append(issue(rule, title, "warning", status, "STEP", pair_id,
                                      {**evidence, "reason": detail},
                                      "确认零件几何条件或补充其他检查方法后重新审核。"))
            pair_results.append({**evidence, "id": pair_id,
                                 "status": status, "detail": detail})
    excluded = total_pairs - checked
    assert success + failed + unsupported == checked
    return {
        "part_count": len(parts), "candidate_pairs": checked, "total_pairs": total_pairs,
        "pair_counts": {"calculated": success, "failed": failed,
                        "not_supported": unsupported, "bbox_excluded": excluded},
        "pair_results": pair_results,
        "length_unit": "not_verified",
        "algorithm_zero_volume_threshold": 1e-6,
        "parts": [{"name": p["name"], "path": p["path"],
                   "geometry": p["geometry"]} for p in parts],
        "findings": findings,
    }
