"""Track referenced DXF title-block instances, including nested inserts."""
from __future__ import annotations

import re

FIELD_NAMES = {
    "part": {"PARTNO", "PARTNUMBER", "PARTNUM", "PARTID", "零件号", "物料编码", "图号"},
    "material": {"MATERIAL", "MAT", "材质", "材料", "材料牌号"},
    "revision": {"REV", "REVISION", "VERSION", "版本", "版本号"},
}


def field_key(value):
    return re.sub(r"[\s\-_]", "", str(value or "")).upper()


def part_key(value):
    """Part identity is exact after stripping exterior whitespace only."""
    return str(value or "").strip()


def material_key(value):
    """Conservative comparison, not material equivalence certification."""
    return str(value or "").strip().casefold()


def extract_attributes(document):
    records, warnings = [], []
    refs_seen = 0

    def walk(ins, layout_name, path, ancestry, depth):
        nonlocal refs_seen
        refs_seen += 1
        if depth > 12 or refs_seen > 15000:
            warnings.append({"path": path, "reason": "depth_or_reference_cap"})
            return
        attrs = {}
        for attr in getattr(ins, "attribs", []):
            label = field_key(attr.dxf.get("tag", ""))
            val = str(attr.dxf.get("text", "")).strip()
            if val:
                for target, aliases in FIELD_NAMES.items():
                    if label in aliases:
                        attrs[target] = val
        if attrs:
            records.append({"layout": layout_name, "instance": path, **attrs})
        name = str(ins.dxf.get("name", ""))
        if name in ancestry:
            warnings.append({"path": path, "reason": "recursive_block_reference"})
            return
        definition = document.blocks.get(name)
        if definition is None:
            warnings.append({"path": path, "reason": "missing_block_definition"})
            return
        for index, child in enumerate(definition.query("INSERT")):
            if child.mcount > 1:
                try:
                    for grid_index, instance in enumerate(child.multi_insert()):
                        walk(instance, layout_name, path + f"/{index}@{grid_index}",
                             ancestry | {name}, depth + 1)
                except Exception as exc:
                    warnings.append({"path": path, "reason": type(exc).__name__})
            else:
                walk(child, layout_name, path + f"/{index}", ancestry | {name}, depth + 1)

    for layout in document.layouts:
        for index, entity in enumerate(layout.query("INSERT")):
            handle = str(entity.dxf.get("handle", ""))
            if entity.mcount > 1:
                try:
                    for grid_index, instance in enumerate(entity.multi_insert()):
                        walk(instance, layout.name, f"{handle}@{grid_index}", frozenset(), 0)
                except Exception as exc:
                    warnings.append({"path": handle, "reason": type(exc).__name__})
            else:
                walk(entity, layout.name, handle or str(index), frozenset(), 0)

    out = {"_dxf_attached": True, "title_records": records, "title_scan_warnings": warnings}
    for field in FIELD_NAMES:
        values = sorted({r[field] for r in records if field in r})
        if values:
            out[field] = values
    return out
