"""Topological preconditions for trustworthy Open CASCADE volume calculations.

BRepGProp.VolumeProperties does not guarantee its input is a closed solid.
A topologically valid shell may be open, so IsValid alone is insufficient.
"""
from __future__ import annotations

import math


def solids_and_validity(shape):
    from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.BRep import BRep_Tool
    from OCP.BRepCheck import BRepCheck_Analyzer

    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solids = []
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    if not solids:
        return [], {"usable": False, "reason": "no_closed_solid",
                    "solid_count": 0}
    for index, solid in enumerate(solids):
        if not BRepCheck_Analyzer(solid).IsValid():
            return [], {"usable": False, "reason": "invalid_solid",
                        "solid_index": index, "solid_count": len(solids)}
        shells = TopExp_Explorer(solid, TopAbs_SHELL)
        shell_count = 0
        while shells.More():
            shell_count += 1
            if not BRep_Tool.IsClosed_s(shells.Current()):
                return [], {"usable": False, "reason": "open_solid_shell",
                            "solid_index": index, "solid_count": len(solids)}
            shells.Next()
        if shell_count == 0:
            return [], {"usable": False, "reason": "solid_without_shell",
                        "solid_index": index, "solid_count": len(solids)}
    return solids, {"usable": True, "solid_count": len(solids),
                    "reason": "valid_closed_solids"}


def intersection_volume(shape):
    """Sum volume of actual solids only; touching faces have zero volume."""
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    solids, meta = solids_and_validity(shape)
    if not solids:
        if meta["reason"] == "no_closed_solid":
            return 0.0, "touching_or_empty"
        raise ValueError("invalid_boolean_result:" + meta["reason"])
    total = 0.0
    for solid in solids:
        mass = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, mass)
        value = float(mass.Mass())
        if not math.isfinite(value) or value < -1e-8:
            raise ValueError("nonfinite_or_negative_volume")
        total += max(value, 0.0)
    return total, "solid_intersection"
