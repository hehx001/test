"""Conservative verification of DXF DIMENSION text semantics.

A dimension's own definition geometry is NOT an associative validation of
the independently drawn edge or hole. Rendered precision is NOT a design tolerance.
"""
from __future__ import annotations

import math
import re

NUMERIC_TEXT = re.compile(
    r'^\s*(?:R|Ø|⌀|Φ|%%[cC])?\s*'
    r'(?P<n>[+-]?(?:\d+-\d+/\d+|\d+/\d+|\d+(?:\.\d+)?))'
    r'\s*(?P<unit>mm|in|")?\s*(?:R|°)?\s*$', re.IGNORECASE
)
UNIT_MM = {1: 25.4, 4: 1.0}


def parse_dimension_text(text: str):
    """Return (number, explicit_unit, display_rounding_halfstep) or None."""
    match = NUMERIC_TEXT.fullmatch(text)
    if match is None:
        return None
    raw = match["n"]
    try:
        if "-" in raw and "/" in raw:
            whole, frac = raw.split("-", 1)
            n, d = map(float, frac.split("/"))
            if d == 0:
                return None
            number = float(whole) + (n / d if float(whole) >= 0 else -n / d)
            resolution = 1.0 / d
        elif "/" in raw:
            n, d = map(float, raw.split("/"))
            if d == 0:
                return None
            number = n / d
            resolution = 1.0 / d
        else:
            number = float(raw)
            resolution = 10 ** (-len(raw.partition(".")[2])) if "." in raw else 1.0
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    unit = (match["unit"] or "").lower().replace('"', "in") or None
    return number, unit, resolution / 2


def check_dimension(dimension, unit_code: int, layout_name: str):
    """Compare displayed number to the dimension entity's own DXF measurement.

    A match does not prove association with any independently drawn edge.
    """
    text = str(dimension.dxf.get("text", "") or "").strip()
    if not text or "<>" in text:
        return "default_or_placeholder", {"shown": text, "reason": "dynamic_label"}
    parsed = parse_dimension_text(text)
    if parsed is None:
        return "custom_unverified", {"shown": text, "reason": "compound_or_unrecognized_text"}
    displayed, explicit_unit, rendering_halfstep = parsed
    dim_type = int(dimension.dxf.get("dimtype", 0)) & 15
    if dim_type not in (0, 1, 2, 3, 4, 5):
        return "custom_unverified", {"shown": text, "reason": "dimension_type_not_supported",
                                     "dim_type": dim_type}
    try:
        actual = dimension.get_measurement()
        if not isinstance(actual, (int, float)) or not math.isfinite(float(actual)):
            raise ValueError("non numeric measurement")
        actual = float(actual)
        overrides = dimension.override()
        lf = overrides.get("dimlfac")
        lf = float(lf if lf is not None else 1.0)
        rnd = overrides.get("dimrnd")
        rnd = float(rnd) if rnd else 0.0
    except (AttributeError, TypeError, ValueError, ArithmeticError, ZeroDivisionError):
        return "custom_unverified", {"shown": text, "reason": "dimension_or_style_unavailable"}
    if not all(math.isfinite(x) for x in (lf, rnd, actual)) or lf == 0 or rnd < 0:
        return "custom_unverified", {"shown": text, "reason": "invalid_dimension_style"}
    angular = dim_type in (2, 5)
    if angular:
        if explicit_unit:
            return "custom_unverified", {"shown": text, "reason": "angle_with_length_unit"}
        expected = actual
    else:
        if lf < 0:
            return "custom_unverified", {"shown": text, "reason": "negative_dimlfac"}
        if explicit_unit:
            if unit_code not in UNIT_MM:
                return "custom_unverified", {"shown": text, "reason": "drawing_units_unknown"}
            if lf != 1.0:
                return "custom_unverified", {"shown": text,
                                             "reason": "explicit_unit_with_dimlfac"}
            source_mm = actual * UNIT_MM[unit_code]
            expected = source_mm / (25.4 if explicit_unit == "in" else 1.0)
        else:
            if unit_code == 0:
                return "custom_unverified", {"shown": text, "reason": "drawing_units_unknown"}
            expected = actual * lf
    if rnd:
        expected = round(expected / rnd) * rnd
    difference = abs(displayed - expected)
    evidence = {
        "shown": text, "parsed_value": displayed, "raw_measurement": round(actual, 8),
        "expected_display_value": round(expected, 8),
        "explicit_unit": explicit_unit, "drawing_insunits": unit_code,
        "dim_type": dim_type, "dimlfac": lf, "dimrnd": rnd,
        "display_rounding_halfstep": rendering_halfstep,
        "source_layout": layout_name, "geometry_association_verified": False,
    }
    if difference <= rendering_halfstep + 1e-8:
        return "numeric_matches", evidence
    return "numeric_conflicts", evidence
