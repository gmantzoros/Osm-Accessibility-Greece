"""Render graph and imagery explanations from saved results and cached assets.

Image-space polygons illustrate detector classes, not geolocated ground truth.
The graph drawing is schematic, and eligible mass defines both usable credit
and the component threshold.
Use --versioned to render inside the isolated corrected reporting bundle.
"""
import argparse
import base64
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from corrected_model_impact import CORRECTED_VERSION, digest
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MPolygon, FancyArrowPatch, FancyBboxPatch, Circle
import mapbox_vector_tile as mvt
from PIL import Image

from aggregate import reporting_paths
from paper_data import verify_reporting_bundle_sources

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "paper", "figures")
SRC = os.path.join(OUT, "src")
D = None
VERSIONED = False
FIGURES = ("fig6_imagery_validation.jpg", "fig7_graph_construction.png")

BASE = "#2E4A66"
HI = "#C1611F"
GREEN = "#3F7350"
SIDEWALK = "#1F6FB2"
KERB = "#7A4FA3"
FAINT = "#D8DEE5"
W = 6.2
DPI = 300

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans"],
    "font.size": 8.5,
    "text.color": "#28323C",
    "figure.dpi": DPI, "savefig.dpi": DPI,
    "savefig.bbox": None, "figure.facecolor": "white",
})

IMAGE_ID = "933896235308839"


def save(fig, name):
    if VERSIONED and Path(OUT, name).resolve() != Path(OUT, name):
        raise ValueError(f"Refusing linked corrected figure output: {name}")
    kw = {"pil_kwargs": {"quality": 95, "optimize": True, "subsampling": 0}} if name.endswith(".jpg") else {}
    fig.savefig(os.path.join(OUT, name), dpi=DPI, bbox_inches=None, **kw)
    plt.close(fig)
    print(f"  wrote {name}")


def rings(b64, w, h):
    """Detection geometry arrives as a vector tile at extent 4096, in image space."""
    out = []
    for layer in mvt.decode(base64.b64decode(b64)).values():
        ext = layer.get("extent", 4096)
        for feat in layer["features"]:
            g = feat["geometry"]
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            for poly in polys:
                for ring in poly:
                    if len(ring) >= 3:
                        # the tile y axis runs upward, the image y axis downward
                        out.append([(x / ext * w, h - y / ext * h) for x, y in ring])
    return out


def cached_frame():
    candidates = []
    for path in Path(SRC).iterdir():
        if IMAGE_ID in path.name and path.suffix.lower() in (".jpg", ".jpeg", ".png"):
            with Image.open(path) as image:
                candidates.append((image.width * image.height, path))
    if not candidates:
        raise ValueError(f"No cached photograph for Mapillary {IMAGE_ID}")
    _, path = max(candidates, key=lambda entry: entry[0])
    with Image.open(path) as image:
        frame = image.convert("RGB")
    print(f"  cached Mapillary {IMAGE_ID}: {frame.width} x {frame.height} pixels ({path.name})")
    return frame


def fig_imagery():
    img = cached_frame()
    meta = json.load(io.open(os.path.join(SRC, f"pick_{IMAGE_ID}.json"), encoding="utf-8"))
    captured = datetime.fromtimestamp(meta["meta"]["captured_at"] / 1000, tz=timezone.utc).date().isoformat()
    if str(meta["meta"]["id"]) != IMAGE_ID or captured != "2024-11-17":
        raise ValueError("Cached Mapillary image identity or capture date mismatch")
    w, h = img.size

    CLASSES = [
        ("construction--flat--sidewalk", SIDEWALK, "sidewalk surface"),
        ("construction--barrier--curb", KERB, "kerb"),
        ("construction--flat--curb-cut", GREEN, "dropped kerb"),
        ("marking--discrete--crosswalk-zebra", HI, "crossing markings"),
    ]

    fig = plt.figure(figsize=(W, 7.2))
    photo_height = W * 0.98 * h / w
    ax = fig.add_axes([0.01, 0.315, 0.98, photo_height / 7.2])
    ax.imshow(img)
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")

    counts = {}
    for value, colour, _ in CLASSES:
        n = 0
        for det in meta["detections"]:
            if det["value"] != value:
                continue
            n += 1
            for ring in rings(det["geometry"], w, h):
                ax.add_patch(MPolygon(ring, closed=True, facecolor=colour, alpha=0.40,
                                      edgecolor=colour, linewidth=1.4, zorder=3))
        counts[value] = n

    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=c, alpha=0.55, edgecolor=c)
               for _, c, _ in CLASSES]
    labels = [f"{lab}  ({counts[v]})" for v, _, lab in CLASSES]
    ax.legend(handles, labels, loc="upper left", frameon=True, framealpha=0.9,
              fontsize=7.8, ncol=2, handlelength=1.0, columnspacing=1.0,
              borderaxespad=0.4, edgecolor="none")
    fig.text(0.015, 0.985, "A. Image-level segmentation, not geolocated ground truth",
             fontsize=9.1, va="top")
    fig.text(0.015, 0.294,
             f"Mapillary {IMAGE_ID} · nglf · CC BY-SA 4.0 · captured {captured}",
             fontsize=7.5)

    ax2 = fig.add_axes([0.015, 0.035, 0.97, 0.225])
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.axis("off")
    ax2.text(0, 0.99, "B. Complementary observation frames", fontsize=9.1, va="top")

    mly = D["mapillary"]
    audit = D["audit"]
    audit_status = ("Primary observations reported separately."
                    if D.get("manual_audit", {}).get("full_primary")
                    else "Observations pending.")
    boxes = [
        (0, 0.48, GREEN, "#F1F5F2",
         "Automated spatial screening",
         f"{mly['intersections']:,.0f} shared-way-node candidates"
         f"\n{mly['junctions_in_image_positive_tiles']:,.0f} in image-positive tiles"
         "\nCrossing / pedestrian-head matches: 25 m"
         "\nTile coverage does not establish visibility;"
         "\nflags are not verified missing features."),
        (0.52, 0.48, HI, "#FAF3EE",
         "Sidewalk-only manual draw",
         f"{audit['n']:,} cases: {audit['n'] // audit['cities']} per principal LAU ({audit['cities']} LAUs)"
         f"\nSidewalk-only draw; {audit_status}"
         "\nLocal visible presence, not whole-way passage."
         "\nNo kerb/tactile arms in the current draw."
         "\nStill images cannot establish sound operation."),
    ]
    for x, bw, col, background, head, body in boxes:
        ax2.add_patch(FancyBboxPatch((x, 0.04), bw, 0.76,
                                     boxstyle="round,pad=0.004,rounding_size=0.02",
                                     facecolor=background, edgecolor=FAINT, linewidth=0.8))
        ax2.text(x + 0.018, 0.72, head, fontsize=7.9, color=col, va="top")
        ax2.text(x + 0.018, 0.56, body, fontsize=7.4, va="top", linespacing=1.35)
    save(fig, "fig6_imagery_validation.jpg")


def fig_graph():
    f = D["feasibility_national"]
    fig = plt.figure(figsize=(W, 5.4))
    a = fig.add_axes([0.015, 0.405, 0.45, 0.535])
    b = fig.add_axes([0.525, 0.405, 0.46, 0.535])
    for ax in (a, b):
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        ax.axis("off")

    a.set_title("A. Crossing evidence", fontsize=9.1, loc="left", pad=8)
    a.add_patch(plt.Rectangle((4, 40), 92, 26, facecolor=FAINT, edgecolor="none", zorder=1))
    a.text(7, 53, "carriageway", fontsize=7.2, color="#5A6B7C", va="center")
    for yy in (36, 70):
        a.plot([4, 96], [yy, yy], color=BASE, linewidth=2.2, zorder=2)
    a.text(4, 73, "sidewalk ways", fontsize=7.2, color=BASE)
    a.plot([46, 46], [36, 70], color=HI, linewidth=2.2, zorder=3)
    a.add_patch(Circle((46, 53), 3.0, facecolor="white", edgecolor=HI, linewidth=1.6, zorder=4))
    a.annotate("crossing way", (46, 68), xytext=(56, 88), fontsize=7.2, color=HI,
               arrowprops=dict(arrowstyle="-", color=HI, linewidth=0.7))
    a.annotate("crossing node", (49, 53), xytext=(58, 24), fontsize=7.2, color=HI,
               arrowprops=dict(arrowstyle="-", color=HI, linewidth=0.7))
    a.add_patch(FancyBboxPatch((2, 2), 96, 13, boxstyle="round,pad=0.4,rounding_size=1.5",
                               facecolor=HI, alpha=0.10, edgecolor=HI, linewidth=0.9))
    a.text(50, 8.5, "Shared crossing members merge evidence;\nprofile resolves conflicting statements",
           fontsize=7.1, ha="center", va="center", color=HI, linespacing=1.4)

    threshold = f["fragmentation"]["min_usable_component_km"]
    b.set_title("B. Scenario graph and eligible mass", fontsize=9.1, loc="left", pad=8)
    steps = [
        (96, 18, BASE, "Way/crossing evidence + profile → way states\nPASS / BLOCK / UNKNOWN"),
        (71, 17, BASE, "Node evidence → transition-gate states\nPASS / BLOCK / UNKNOWN; zero-length gates"),
        (47, 16, HI, "Pessimistic: PASS ways and gates only\nOptimistic: PASS + UNKNOWN ways and gates"),
        (24, 19, GREEN, "Component C receives eligible usable credit\n"
                        + rf"only if its eligible mass $L_e(C)\geq {threshold:g}$ km"),
    ]
    for y, hgt, col, text in steps:
        b.add_patch(FancyBboxPatch((1, y - hgt), 98, hgt,
                                   boxstyle="round,pad=0.4,rounding_size=1.2",
                                   facecolor=col, alpha=0.10, edgecolor=col, linewidth=0.9))
        b.text(50, y - hgt / 2, text, fontsize=7.1, ha="center", va="center",
               color="#28323C", linespacing=1.4)
    for y0, y1 in ((77, 73), (53, 49), (30, 26)):
        b.add_patch(FancyArrowPatch((50, y0), (50, y1), arrowstyle="-|>", mutation_scale=6,
                                    color="#5A6B7C", linewidth=0.8))

    footer = fig.add_axes([0.015, 0.04, 0.97, 0.315])
    footer.axis("off")
    footer.text(0.01, 0.87,
                r"$U_s(\%)=100\,\frac{\sum_{C\in\mathcal{C}_s}L_e(C)\,\mathbf{1}\{L_e(C)\geq\tau\}}{L_e(G)}$",
                fontsize=10.8, va="center")
    footer.text(0.01, 0.65,
                rf"$L_e(C)$ supplies both usable credit and threshold mass ($\tau={threshold:g}$ eligible km).",
                fontsize=8.0)
    footer.text(0.01, 0.47,
                "Auxiliary geometry may connect; it supplies neither usable credit nor threshold mass.",
                fontsize=7.6)
    footer.text(0.01, 0.29,
                r"Gates have zero length; the denominator is total eligible network mass $L_e(G)$.",
                fontsize=7.8)
    footer.text(0.01, 0.09,
                f"Conditional share: pessimistic {f['pess_pct']:.1f}%  |  optimistic {f['opt_pct']:.1f}%"
                f"  |  gap {f['gap_pct']:.1f} pp", fontsize=8.1, color=HI)
    save(fig, "fig7_graph_construction.png")


def main(*, versioned=False, root=ROOT):
    global ROOT, OUT, SRC, D, VERSIONED
    paths = reporting_paths(root, versioned)
    ROOT = os.fspath(paths["root"])
    OUT = os.fspath(paths["figures"])
    SRC = os.path.join(ROOT, "paper", "figures", "src")
    VERSIONED = versioned
    with io.open(paths["bundle"], encoding="utf-8") as stream:
        D = json.load(stream)
    if versioned:
        verify_reporting_bundle_sources(paths, D)
    os.makedirs(OUT, exist_ok=True)
    fig_imagery()
    fig_graph()
    if versioned:
        manifest = {
            "schema_version": 1,
            "model_version": CORRECTED_VERSION,
            "paper_data_sha256": digest(paths["bundle"]),
            "script_sha256": digest(paths["root"] / "analysis" / "figures_explain.py"),
            "figures": {name: digest(paths["figures"] / name) for name in FIGURES},
        }
        (paths["figures"] / "figures_explain_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versioned", action="store_true",
                        help="Read only the corrected unified bundle; write figures inside its paper directory")
    main(versioned=parser.parse_args().versioned)
