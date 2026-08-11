#!/usr/bin/env python3
"""
makera2flatcam.py — convert Makera Carvera / Carvera Air tool libraries
(Fusion 360 *.tools, free from github.com/MakeraInc/CarveraProfiles)
into a FlatCAM Beta 8.9x Tools Database file (*.FlatDB).

Why a template: FlatCAM renamed its tool-data keys between 8.994 and 8.995
(e.g. "cutz" -> "tools_mill_cutz"). Instead of hardcoding a schema, this script
reads ONE tool you exported from your own FlatCAM and reuses its exact keys.

Usage
-----
  1) FlatCAM: Edit -> Tools Database -> Add Tool -> Export DB  -> template.FlatDB
  2) python3 makera2flatcam.py \
         --tools "CarveraProfiles/CAM_Post_Processors/Fusion360-profiles/Tool Files"/*.tools \
         --template template.FlatDB \
         --material PCB \
         --out makera_pcb.FlatDB
  3) FlatCAM: Edit -> Tools Database -> Import DB -> makera_pcb.FlatDB
"""

import argparse
import copy
import json
import os
import sys
import zipfile

# Makera preset field -> list of candidate FlatCAM key names (first match in the
# template wins). Covers 8.994 and 8.995 naming.
KEY_CANDIDATES = {
    "feedrate":     ["feedrate", "tools_mill_feedrate", "feedrate_xy"],
    "feedrate_z":   ["feedrate_z", "tools_mill_feedrate_z", "feedrate_plunge"],
    "feedrate_rapid": ["feedrate_rapid", "tools_mill_feedrate_rapid"],
    "spindlespeed": ["spindlespeed", "tools_mill_spindlespeed"],
    "cutz":         ["cutz", "tools_mill_cutz"],
    "depthperpass": ["depthperpass", "tools_mill_depthperpass"],
    "multidepth":   ["multidepth", "tools_mill_multidepth"],
    "travelz":      ["travelz", "tools_mill_travelz"],
    "vtipdia":      ["vtipdia", "tools_mill_vtipdia"],
    "vtipangle":    ["vtipangle", "tools_mill_vtipangle"],
    "tooldia":      ["tooldia", "tools_mill_tooldia"],
    "name":         ["name"],
    # drill workflow uses its own namespace in FlatCAM
    "d_cutz":         ["tools_drill_cutz"],
    "d_feedrate_z":   ["tools_drill_feedrate_z"],
    "d_spindlespeed": ["tools_drill_spindlespeed"],
    "d_depthperpass": ["tools_drill_depthperpass"],
    "d_multidepth":   ["tools_drill_multidepth"],
    # stepover -> overlap percentage
    "ncc_overlap":   ["tools_ncc_overlap"],
    "paint_overlap": ["tools_paint_overlap"],
}


def pick_key(template_data, logical):
    for k in KEY_CANDIDATES[logical]:
        if k in template_data:
            return k
    return None


def set_val(data, logical, value):
    k = pick_key(data, logical)
    if k is not None and value is not None:
        data[k] = value
        return True
    return False


def load_tools(path):
    """A .tools file is a zip containing tools.json."""
    with zipfile.ZipFile(path) as z:
        name = "tools.json" if "tools.json" in z.namelist() else z.namelist()[0]
        return json.loads(z.read(name).decode("utf-8"))["data"]


# If the exact material preset is absent on a tool, fall back down this chain
# instead of grabbing preset[0] (a wood preset on a copper job = wrong feeds).
FALLBACK = {
    "pcb":      ["pcb", "copper", "brass", "aluminum", "plastic"],
    "copper":   ["copper", "pcb", "brass", "aluminum"],
    "brass":    ["brass", "copper", "aluminum"],
    "aluminum": ["aluminum", "brass", "copper"],
    "hardwood": ["hardwood", "softwood", "plastic"],
    "softwood": ["softwood", "hardwood", "plastic"],
    "plastic":  ["plastic", "softwood", "hardwood"],
}


def pick_preset(tool, material):
    presets = tool.get("start-values", {}).get("presets", [])
    if not presets:
        return None
    chain = FALLBACK.get(material.lower(), [material.lower()])
    for want in chain:
        for p in presets:
            if p.get("name", "").lower() == want:
                return p
    return None                # no sane match -> skip this tool


def classify(tool):
    """Return (flatcam_type, flatcam_shape) from Makera geometry/description."""
    g = tool.get("geometry", {})
    d = tool.get("description", "").lower()
    tip = g.get("tip-diameter")
    ta = g.get("TA", 0) or 0
    if "drill" in d:
        return "Drill", "C1"
    if tip and ta > 0:
        return "Iso", "V"      # V-bit -> isolation routing
    if "ball" in d:
        return "Rough", "B"
    return "Rough", "C1"


def convert(tools_files, template_path, material, out_path, cutz, drillz, verbose):
    with open(template_path) as f:
        template = json.load(f)
    if not template:
        sys.exit("Template FlatDB is empty — add at least one tool in FlatCAM first.")
    base_entry = copy.deepcopy(template[sorted(template.keys())[0]])
    base_data = base_entry.get("data", {})
    if not base_data:
        sys.exit("Template entry has no 'data' block — wrong FlatCAM version?")

    out, idx = {}, 0
    for tf in tools_files:
        for t in load_tools(tf):
            g = t.get("geometry", {})
            dc = g.get("DC")
            if not dc:
                continue
            p = pick_preset(t, material)
            if p is None:
                continue
            ttype, shape = classify(t)
            idx += 1

            e = copy.deepcopy(base_entry)
            d = e["data"]
            name = t.get("description", f"Makera {idx}")[:60]

            e["name"] = name
            e["tooldia"] = round(float(dc), 4)
            if "type" in e:
                e["type"] = ttype
            if "tool_type" in e:
                e["tool_type"] = shape

            set_val(d, "name", name)
            set_val(d, "tooldia", round(float(dc), 4))
            step = p.get("stepdown")
            rpm = int(round(p.get("n", 0)))

            if ttype == "Drill":
                # Excellon workflow reads the tools_drill_* namespace
                set_val(d, "d_feedrate_z", round(p.get("v_f_plunge", 0), 1))
                set_val(d, "d_spindlespeed", rpm)
                set_val(d, "d_cutz", drillz)
                if step:
                    set_val(d, "d_depthperpass", round(float(step), 3))
                    set_val(d, "d_multidepth", True)
            else:
                set_val(d, "feedrate", round(p.get("v_f", 0), 1))
                set_val(d, "feedrate_z", round(p.get("v_f_plunge", 0), 1))
                set_val(d, "spindlespeed", rpm)
                set_val(d, "cutz", cutz)
                if step:
                    set_val(d, "depthperpass", round(float(step), 3))
                    set_val(d, "multidepth", True)

            if shape == "V":
                set_val(d, "vtipdia", g.get("tip-diameter"))
                set_val(d, "vtipangle", round(2 * g.get("TA", 0), 2))
            else:
                # Makera stepover is absolute mm; FlatCAM wants overlap %.
                # Skipped for V-bits: their effective width is the tip, not DC.
                so = p.get("stepover")
                if so and dc:
                    ov = round(max(0.0, min(99.9, (1 - so / dc) * 100)), 1)
                    set_val(d, "ncc_overlap", ov)
                    set_val(d, "paint_overlap", ov)

            out[str(idx)] = e
            if verbose:
                print(f"[{idx:3d}] {name[:48]:50s} d={dc:<6} "
                      f"F={p.get('v_f',0):.0f} S={p.get('n',0):.0f} "
                      f"({p.get('name')})")

    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n{idx} tools -> {out_path}  (preset: {material})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", nargs="+", required=True, help="Makera *.tools files")
    ap.add_argument("--template", required=True, help=".FlatDB exported from your FlatCAM")
    ap.add_argument("--material", default="PCB",
                    help="PCB | Aluminum | Brass | Copper | Hardwood | Softwood | Plastic | Carbon Fiber")
    ap.add_argument("--out", default="makera_tools.FlatDB")
    ap.add_argument("--cutz", type=float, default=-0.05,
                    help="Cut Z in mm for isolation (FlatCAM sign: negative)")
    ap.add_argument("--drillz", type=float, default=-1.8,
                    help="Cut Z in mm for drilling (negative), e.g. board thickness + 0.2")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    missing = [t for t in a.tools if not os.path.exists(t)]
    if missing:
        sys.exit(f"Not found: {missing}")
    convert(a.tools, a.template, a.material, a.out, a.cutz, a.drillz, a.verbose)


if __name__ == "__main__":
    main()