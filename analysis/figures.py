"""Render saved-result figures on a fixed 6.2-inch, 300-dpi journal canvas.

Survey curves use actual usable gains and validated complete ranked prefixes.
No census metrics, source rankings or graph scenarios are recomputed.
Use --versioned to render the verified unified corrected bundle in isolation.
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
from aggregate import reporting_paths
from corrected_model_impact import CORRECTED_VERSION, digest
from paper_data import verify_reporting_bundle_sources
from targeting_distance import SOURCES, analyze_sources, write_comparison

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from matplotlib.lines import Line2D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "paper", "figures")
D = None
VERSIONED = False
FIGURES = ("fig1_presence_affirmation.png", "fig2_cities_attica.png", "fig3_decidability.png",
           "fig4_cross_country.png", "fig5_targeting_curve.png", "fig8_targeting_distance.png")

BASE = "#2E4A66"
HI = "#C1611F"
GREEN = "#3F7350"
MUTE = "#9AA5B1"
FAINT = "#D8DEE5"
W = 6.2
DPI = 300

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans"],
    "font.size": 8.5,
    "axes.edgecolor": "#5A6B7C",
    "axes.linewidth": 0.7,
    "axes.labelcolor": "#28323C",
    "text.color": "#28323C",
    "xtick.color": "#5A6B7C",
    "ytick.color": "#5A6B7C",
    "xtick.major.width": 0.7,
    "ytick.major.width": 0.7,
    "figure.dpi": DPI,
    "savefig.dpi": DPI,
    "savefig.bbox": None,
    "figure.facecolor": "white",
})


def tidy(ax, grid="x"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if grid:
        ax.grid(axis=grid, color=FAINT, linewidth=0.6, zorder=0)
        ax.set_axisbelow(True)


def save(fig, name):
    p = os.path.join(OUT, name)
    if VERSIONED and Path(p).resolve() != Path(p):
        raise ValueError(f"Refusing linked corrected figure output: {p}")
    fig.savefig(p, dpi=DPI, bbox_inches=None)
    plt.close(fig)
    print(f"  wrote {name}")


def fig_presence():
    vd = D["value_distributions"]

    def merged(key, keep):
        """Collapse duplicate value labels, then split into keep / other."""
        acc = {}
        for e in vd[key]["values"]:
            label = "no / none" if e["value"] in ("no", "none") else e["value"]
            acc[label] = acc.get(label, 0.0) + e["pct"]
        out = [(k, acc.get(k, 0.0)) for k in keep]
        out.append(("other", max(0.0, 100.0 - sum(v for _, v in out))))
        return out

    def label(title, key):
        value = vd[key]
        return f"{title}\n{value['total']:,.0f} {value['unit']} · {value['universe']}"

    rows = [
        (label("sidewalk", "sidewalk"), merged("sidewalk", ["no / none", "both", "right", "left"])),
        (label("tactile paving", "tactile_paving"), merged("tactile_paving", ["no / none", "yes", "partial"])),
        (label("kerb", "kerb"), merged("kerb", ["raised", "lowered", "flush"])),
        (label("lit", "lit"), merged("lit", ["no / none", "yes"])),
    ]

    fig, ax = plt.subplots(figsize=(W, 3.45))
    fig.subplots_adjust(left=0.29, right=0.985, bottom=0.31, top=0.89)
    ypos = range(len(rows))
    for y, (row_label, parts) in zip(ypos, rows):
        left = 0.0
        for value, pct in parts:
            if pct <= 0:
                continue
            absent = value == "no / none"
            ax.barh(y, pct, left=left, height=0.62, zorder=2,
                    color=HI if absent else (FAINT if value == "other" else BASE),
                    edgecolor="white", linewidth=0.8)
            if pct >= 7:
                ax.text(left + pct / 2, y, f"{value}\n{pct:.1f}%", ha="center", va="center",
                        fontsize=7.6, color="#28323C" if value == "other" else "white")
            left += pct

    ax.set_yticks(list(ypos))
    ax.set_yticklabels([r[0] for r in rows], fontsize=8.1, linespacing=1.3)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xticks(range(0, 101, 20))
    ax.set_xlabel("Share within canonical-key tagged universe (%)", fontsize=8.1)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}"))
    ax.get_xticklabels()[-1].set_horizontalalignment("right")
    tidy(ax)
    fig.text(0.015, 0.975, "Recorded values within their respective feature universes",
             fontsize=9.3, va="top")
    handles = [plt.Rectangle((0, 0), 1, 1, color=colour) for colour in (HI, BASE, FAINT)]
    fig.legend(handles, ["Literal no / none", "Other named values", "Other values"],
               loc="lower center", bbox_to_anchor=(0.5, 0.12), ncol=3,
               frameon=False, fontsize=7.6, handlelength=1.2, columnspacing=1.5)
    fig.text(0.015, 0.087, "Colours group recorded values, not profile acceptance.", fontsize=7.5)
    fig.text(0.015, 0.042,
             "Raised kerbs are not universally negative for blind/visually impaired users.",
             fontsize=7.5)
    save(fig, "fig1_presence_affirmation.png")


def fig_cities():
    city_csv = reporting_paths(ROOT, VERSIONED)["urban"] / "top_cities.csv"
    rows = list(csv.DictReader(io.open(city_csv, encoding="utf-8")))
    selected_n = len(rows)
    rows = [r for r in rows if float(r["eligible_km"]) > 0 and r["pess_usable_pct"] != ""]
    if not rows:
        raise ValueError("No selected dimos groups with eligible network")
    ATT = {"45", "46", "47", "48", "49", "50", "51"}
    for r in rows:
        r["att"] = r["dimos_code"].zfill(4)[:2] in ATT
        r["v"] = float(r["pess_usable_pct"])
    rows.sort(key=lambda r: -r["v"])
    rank = {r["dimos_code"]: i for i, r in enumerate(rows)}

    fig, ax = plt.subplots(figsize=(W, 3.8))
    fig.subplots_adjust(left=0.12, right=0.985, bottom=0.26, top=0.89)
    ax.bar(range(len(rows)), [r["v"] for r in rows], width=0.78, zorder=2,
           color=[HI if r["att"] else BASE for r in rows])

    att = D["urban"]["attica"]
    median_handles, median_labels = [], []
    for key, col, name in (("attica", HI, "Attica"), ("rest", BASE, "Elsewhere")):
        if key not in att:
            continue
        med = att[key]["pessimistic_usable_pct"]
        if med is None:
            continue
        ax.axhline(med, color=col, linewidth=0.9, linestyle=(0, (4, 2)), zorder=3)
        median_handles.append(Line2D([], [], color=col, linestyle=(0, (4, 2)), linewidth=0.9))
        median_labels.append(f"{name} median ({med:.2f}%)")

    labels = [("4501", "Athens", (0.22, 0.90)),
              ("0701", "Thessaloniki", (0.36, 0.78)),
              ("4808", "Palaio Faliro", (0.53, 0.67)),
              ("3701", "Patras", (0.67, 0.52)),
              ("7101", "Heraklion", (0.82, 0.40))]
    for code, text, position in labels:
        if code not in rank:
            continue
        k = rank[code]
        ax.annotate(text, (k, rows[k]["v"]), textcoords="axes fraction", xytext=position,
                    ha="left", va="bottom", fontsize=7.8,
                    linespacing=1.15, color=HI if rows[k]["att"] else BASE,
                    arrowprops=dict(arrowstyle="-", linewidth=0.6, shrinkA=0, shrinkB=2,
                                    color=HI if rows[k]["att"] else BASE))

    ax.set_xlim(-1, len(rows))
    ax.set_ylim(0, max(r["v"] for r in rows) * 1.30 + 1)
    ax.set_xticks([])
    ax.set_ylabel("Pessimistic eligible usable share (%)", fontsize=8.2)
    ax.set_xlabel(f"Municipality-prefix groups with eligible network, ranked (n = {len(rows)}/{selected_n})",
                  fontsize=8.0, labelpad=5)
    tidy(ax, grid="y")
    handles = [plt.Rectangle((0, 0), 1, 1, color=HI), plt.Rectangle((0, 0), 1, 1, color=BASE)]
    att_n = sum(r["att"] for r in rows)
    fig.legend(handles + median_handles,
               [f"Attica ({att_n} groups)", f"Elsewhere ({len(rows) - att_n} groups)"] + median_labels,
               frameon=False, fontsize=7.6, loc="lower center",
               bbox_to_anchor=(0.5, 0.045), ncol=2, handlelength=1.6, columnspacing=2.0)
    fig.text(0.015, 0.975, "Conditional usable share across population-selected groups",
             fontsize=9.3, va="top")
    save(fig, "fig2_cities_attica.png")


def fig_decidability():
    f = D["feasibility_national"]
    blk, unk = f["block_pct"], f["unknown_pct"]
    pas = f["pass_pct"]
    usable = f["pess_pct"]
    u, wk = f["undecidable"], f["walkable_km"]

    fig = plt.figure(figsize=(W, 5.6))
    a1 = fig.add_axes([0.29, 0.70, 0.69, 0.14])
    a2 = fig.add_axes([0.29, 0.14, 0.69, 0.43])

    left = 0.0
    for label, pct, col in [("PASS", pas, BASE), ("BLOCK", blk, "#6E7F8F"),
                            ("UNKNOWN", unk, HI)]:
        a1.barh(0, pct, left=left, height=0.5, color=col, edgecolor="white",
                linewidth=0.8, zorder=2)
        if pct > 20:
            a1.text(left + pct / 2, 0, f"{label}  {pct:.1f}%", ha="center", va="center",
                    color="white", fontsize=8)
        else:
            anchor = 0.0 if label == "PASS" else 18.0
            ha = "left"
            a1.plot([left + pct / 2, anchor + 1.0], [-0.25, -0.33],
                    color=col, linewidth=0.6, zorder=3, clip_on=False)
            a1.annotate(f"{label} {pct:.1f}%", (anchor + 1.4, -0.36), ha=ha, va="top",
                        fontsize=7.8, color=col, annotation_clip=False)
        left += pct
    threshold = f["fragmentation"]["min_usable_component_km"]
    fig.text(0.015, 0.975, "A. Profile-conditioned way-length states", fontsize=9.3, va="top")
    fig.text(0.015, 0.918, "States use modelled way length; node-transition gates are separate.",
             fontsize=8.0, color="#5A6B7C")
    fig.text(0.015, 0.879, f"Pessimistic usable share: {usable:.1f}% of eligible length; "
             f"threshold {threshold:g} eligible km.", fontsize=8.0, color="#5A6B7C")
    a1.set_xlim(0, 100)
    a1.set_xticks(range(0, 101, 20))
    a1.get_xticklabels()[-1].set_horizontalalignment("right")
    a1.set_ylim(-0.75, 0.5)
    a1.set_yticks([])
    a1.set_xlabel(f"Share of {wk:,.0f} km of modelled way length (%)", fontsize=8.0, labelpad=4)
    tidy(a1, grid=None)
    a1.spines["left"].set_visible(False)

    causes = [("no sidewalk statement\non an ordinary road", u["road_sidewalk_km"]),
              ("track, cycleway, bridleway\n(class-policy UNKNOWN)", u["graph_only_km"]),
              ("steps with no\nhandrail statement", u["steps_km"]),
              ("crossing attributes\nmissing", u["crossing_attrs_km"]),
              ("other pedestrian\nmissing evidence", u["pedestrian_other_km"]),
              ("other unresolved\nevidence", u["other_km"])]
    ys = range(len(causes))
    a2.barh(list(ys), [c[1] for c in causes], height=0.6, color=BASE, zorder=2)
    for y, (_, km) in zip(ys, causes):
        share = 100 * km / wk
        share_label = f"{share:.1f}%" if share >= 0.1 or share == 0 else "<0.1%"
        a2.text(km + wk * 0.012, y, f"{km:,.0f} km  ·  {share_label}", va="center", fontsize=7.7)
    a2.set_yticks(list(ys))
    a2.set_yticklabels([c[0] for c in causes], fontsize=7.8, linespacing=1.2)
    a2.invert_yaxis()
    a2.set_xlim(0, max(c[1] for c in causes) * 1.62 + 1)
    a2.set_xlabel("Modelled way length (km)", fontsize=8.2, labelpad=4)
    a2.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
    tidy(a2)
    fig.text(0.015, 0.615, f"B. UNKNOWN length ({unk:.1f}%): missing evidence and class policy",
             fontsize=9.1, va="top")
    fig.text(0.015, 0.035, "UNKNOWN is not a verdict on physical accessibility.", fontsize=7.7)
    save(fig, "fig3_decidability.png")


def fig_countries():
    rows = sorted(D["cross_country"]["rows"], key=lambda r: r["pess_usable_pct"])
    fig, ax = plt.subplots(figsize=(W, max(3.8, 0.9 + 0.255 * len(rows))))
    fig.subplots_adjust(left=0.19, right=0.985, bottom=0.15, top=0.89)
    for i, r in enumerate(rows):
        el = r["code"] == "EL"
        col = HI if el else BASE
        ax.plot([r["pess_usable_pct"], r["opt_usable_pct"]], [i, i],
                color=col if el else MUTE, linewidth=1.6 if el else 1.1, zorder=2,
                solid_capstyle="round")
        ax.plot(r["pess_usable_pct"], i, "o", color=col, markersize=4.6, zorder=3)
        ax.plot(r["opt_usable_pct"], i, "o", color=col, markersize=4.6, zorder=3,
                markerfacecolor="white", markeredgewidth=1.1)
        ax.text(113, i, f"{r['gap_pct']:.1f}", va="center", ha="right",
                fontsize=8.0, color=col if el else "#5A6B7C")

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([("Greece" if r["code"] == "EL" else r["country"]) for r in rows],
                       fontsize=8.2)
    for lbl, r in zip(ax.get_yticklabels(), rows):
        if r["code"] == "EL":
            lbl.set_color(HI)
            lbl.set_fontweight("bold")
    ax.set_xlim(0, 116)
    ax.set_ylim(-1, len(rows) + 0.4)
    ax.text(113, len(rows) - 0.15, "Gap (pp)", ha="right", fontsize=7.8, color="#5A6B7C")
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    ax.set_xlabel("Eligible-network usable share (%)", fontsize=8.5, labelpad=5)
    tidy(ax)
    fig.text(0.015, 0.975, f"Conditional endpoints in {len(rows)} selected countries",
             fontsize=9.3, va="top")
    fig.legend([Line2D([], [], marker="o", color=BASE, linestyle="none"),
                Line2D([], [], marker="o", color=BASE, markerfacecolor="white", linestyle="none")],
               ["Pessimistic scenario", "Optimistic scenario"], ncol=2,
               loc="lower center", bbox_to_anchor=(0.55, 0.028), frameon=False, fontsize=8.0)
    save(fig, "fig4_cross_country.png")


CURVE_STYLES = {
    "static_initial_consolidation": (BASE, "-", "o"),
    "length_only": (HI, "--", "s"),
    "consolidation_per_km": (GREEN, "-.", "^"),
}


def fig_targeting(report, curves):
    fig, ax = plt.subplots(figsize=(W, 3.55))
    fig.subplots_adjust(left=0.13, right=0.98, bottom=0.22, top=0.88)
    for key, curve in curves.items():
        colour, style, marker = CURVE_STYLES[key]
        xs = [0] + [point.tasks for point in curve.points]
        ys = [0.0] + [float(point.usable_gain_km) for point in curve.points]
        if key == "static_initial_consolidation":
            ax.fill_between(xs, 0, ys, step="post", color=colour, alpha=0.09)
        ax.step(xs, ys, where="post", color=colour, linestyle=style, linewidth=1.4,
                label=f"{SOURCES[key][2]} ({ys[-1]:,.0f} km)")
        ax.plot(xs[-1], ys[-1], marker=marker, color=colour, markersize=4.0)
    main = curves["static_initial_consolidation"]
    k = min(1000, len(main.points))
    gain = float(main.points[k - 1].usable_gain_km)
    proportion = 100 * gain / float(main.points[-1].usable_gain_km)
    ax.plot(k, gain, "o", color=BASE, markersize=4.0)
    ax.annotate(f"{k:,} static-score tasks:\n{gain:,.0f} km; {proportion:.1f}% of its saved total",
                (k, gain), xytext=(14, -31), textcoords="offset points",
                fontsize=7.7, color=BASE, linespacing=1.35,
                arrowprops=dict(arrowstyle="-", color=BASE, linewidth=0.6))
    ax.legend(frameon=False, fontsize=7.7, loc="upper left", handlelength=2.2)
    ax.set_xlim(0, max(len(curve.points) for curve in curves.values()) * 1.015)
    ax.set_ylim(0, max(float(curve.points[-1].usable_gain_km) for curve in curves.values()) * 1.12)
    ax.set_xlabel("Selected survey tasks (ranked whole-way prefixes)", fontsize=8.1, labelpad=5)
    ax.set_ylabel("Conditional eligible usable gain (km)", fontsize=8.2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
    tidy(ax, grid="y")
    fig.text(0.015, 0.975, "All-positive way-state scenarios with fixed node gates",
             fontsize=9.3, va="top")
    fig.text(0.015, 0.04, "Task counts are not survey-time or realised-yield estimates.", fontsize=7.6)
    save(fig, "fig5_targeting_curve.png")


def fig_targeting_distance(report, curves):
    upper = report["common_max_survey_km"]
    fig = plt.figure(figsize=(W, 4.85))
    ax = fig.add_axes([0.13, 0.47, 0.85, 0.425])
    for key, curve in curves.items():
        colour, style, marker = CURVE_STYLES[key]
        xs, ys = curve.staircase(upper)
        ax.step(xs, ys, where="post", color=colour, linestyle=style,
                linewidth=1.5, label=SOURCES[key][2])
        prefix = curve.prefix(upper)
        ax.plot(float(prefix.survey_km), float(prefix.usable_gain_km),
                marker=marker, color=colour, markersize=4.5, clip_on=False)
    ax.set_xlim(0, upper)
    ax.set_ylim(0, max(float(curve.prefix(upper).usable_gain_km) for curve in curves.values()) * 1.12)
    ticks = [float(value) for value in (0, 100, 200, 300, 400) if value < upper] + [upper]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{value:g}" if value != upper else f"{upper:.1f}" for value in ticks],
                       fontsize=7.9)
    ax.get_xticklabels()[-1].set_horizontalalignment("right")
    ax.set_xlabel("Survey-distance budget (selected whole-way extents, km)", fontsize=8.1, labelpad=5)
    ax.set_ylabel("Conditional eligible\nusable gain (km)", fontsize=8.2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(frameon=False, fontsize=7.7, loc="upper left", handlelength=2.2)
    tidy(ax, grid="y")
    fig.text(0.015, 0.975, "Equal recorded survey-distance budgets", fontsize=9.3, va="top")
    fig.text(0.015, 0.925,
             f"All-positive way-state overrides; node gates fixed; threshold {report['component_threshold_km']:g} eligible km.",
             fontsize=7.7, color="#5A6B7C")
    fig.text(0.015, 0.30, f"Common maximum: {upper:,.3f} km budget (complete prefixes)",
             fontsize=8.3)
    table = fig.add_axes([0.015, 0.125, 0.97, 0.12])
    table.axis("off")
    for x, text, alignment in ((0, "Ordering", "left"), (0.57, "Tasks", "right"),
                                (0.79, "Used (km)", "right"), (0.995, "Gain (km)", "right")):
        table.text(x, 1.0, text, ha=alignment, fontsize=7.8, fontweight="bold")
    endpoint = report["comparisons"][-1]["strategies"]
    for y, (key, prefix) in zip((0.70, 0.37, 0.04), endpoint.items()):
        table.text(0, y, SOURCES[key][2], fontsize=7.8, color=CURVE_STYLES[key][0])
        table.text(0.57, y, f"{prefix['selected_tasks']:,}", ha="right", fontsize=7.8)
        table.text(0.79, y, f"{prefix['used_survey_km']:,.3f}", ha="right", fontsize=7.8)
        table.text(0.995, y, f"{prefix['cumulative_usable_gain_km']:,.3f}", ha="right", fontsize=7.8)
    fig.text(0.015, 0.075, "Ranked prefixes only: no fractional ways, skipped next ways or extrapolation.",
             fontsize=7.4)
    fig.text(0.015, 0.04, "Equal distance is not equal task count, survey time or realised yield.",
             fontsize=7.4)
    save(fig, "fig8_targeting_distance.png")


AUDIT_FIGURE = "fig9_sidewalk_audit.png"


def fig_sidewalk_audit():
    """Response mix per fully annotated audit community; skipped without such communities."""
    m = D.get("manual_audit", {})
    if not m.get("available"):
        return False
    rate = next(r for r in m["rates"] if r["arm"] == "sidewalk" and r["metric"] == "sidewalk_present")
    sub = rate.get("completed_cities")
    if not (sub or m["full_primary"]):
        return False
    counts = {c["city"]: c for c in m["city_arm_counts"] if c["arm"] == "sidewalk"}
    rows = []
    for city in (sub["cities"] if sub else sorted(rate["by_city"])):
        seen = counts[city]["recent_responses"]["sidewalk"]
        result = rate["by_city"][city]
        both = seen.get("both", 0)
        one = sum(seen.get(k, 0) for k in ("left", "right", "present_side_unspecified"))
        if both + one != result["positive"]:
            raise ValueError(f"Audit response counts disagree with outcomes: {city}")
        unverifiable = sum(result["missing_reasons"].get(k, 0) for k in ("unverifiable", "missing_outcome"))
        old = sum(result["missing_reasons"].values()) - unverifiable
        judged = result["positive"] + result["negative"]
        rows.append((m["city_labels"][city], [both, one, result["negative"], old, unverifiable],
                     result["positive"], judged))
    rows.sort(key=lambda r: (-r[2] / r[3] if r[3] else 1, r[0]))

    row_h = 0.24 if len(rows) <= 15 else 0.13
    height = 1.3 + row_h * len(rows)
    top, bottom = 1 - 0.95 / height, 0.42 / height
    fig = plt.figure(figsize=(W, height))
    ax = fig.add_axes([0.2, bottom, 0.68, top - bottom])
    segments = (("Both sides", BASE), ("One side", "#7E9CBB"), ("No sidewalk", HI),
                ("Imagery >5 years old", "#8A939C"), ("Unverifiable", "#C9CFD6"))
    ys = list(range(len(rows)))[::-1]
    for i, (name, colour) in enumerate(segments):
        ax.barh(ys, [r[1][i] for r in rows], left=[sum(r[1][:i]) for r in rows], height=0.7,
                color=colour, edgecolor="white", linewidth=1.0, label=name, zorder=2)
    for y, (_, _, positive, judged) in zip(ys, rows):
        ax.text(1.03, y, f"{positive}/{judged}", transform=ax.get_yaxis_transform(),
                va="center", fontsize=7.6)
    ax.text(1.03, 1.01, "Present /\njudgeable", transform=ax.transAxes, va="bottom",
            fontsize=7.0, color="#5A6B7C")
    total = max(sum(r[1]) for r in rows)
    ax.set_xlim(0, total)
    ax.set_xticks(range(0, total + 1, 2))
    ax.set_yticks(ys)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_yticklabels([r[0] for r in rows], fontsize=7.8)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Sampled locations per community", fontsize=8.1)
    tidy(ax, grid="x")
    ax.spines["left"].set_visible(False)
    fig.text(0.015, 1 - 0.1 / height, "Visible sidewalks at sampled locations on untagged drivable ways",
             fontsize=9.3, va="top")
    fig.text(0.015, 1 - 0.34 / height,
             f"{sum(sum(r[1]) for r in rows)} points in {len(rows)} communities. "
             f"Recency: imagery within five years of {m['reference_date']}.",
             fontsize=7.7, color="#5A6B7C", va="top")
    fig.legend(loc="upper left", bbox_to_anchor=(0.005, 1 - 0.5 / height), ncol=5, frameon=False,
               fontsize=7.4, handlelength=1.2, columnspacing=1.2)
    save(fig, AUDIT_FIGURE)
    return True


def main(*, versioned=False, root=ROOT):
    global ROOT, OUT, D, VERSIONED
    paths = reporting_paths(root, versioned)
    ROOT = os.fspath(paths["root"])
    OUT = os.fspath(paths["figures"])
    VERSIONED = versioned
    with io.open(paths["bundle"], encoding="utf-8") as stream:
        D = json.load(stream)
    if versioned:
        verify_reporting_bundle_sources(paths, D)
        report, curves = analyze_sources(ROOT, versioned=True)
        if report != D.get("targeting_distance"):
            raise ValueError("Corrected figure worklists disagree with the validated reporting bundle")
    else:
        report, curves = write_comparison(ROOT)
    os.makedirs(OUT, exist_ok=True)
    print(f"figures -> {OUT}")
    fig_presence()
    fig_cities()
    fig_decidability()
    fig_countries()
    fig_targeting(report, curves)
    fig_targeting_distance(report, curves)
    names = FIGURES + ((AUDIT_FIGURE,) if fig_sidewalk_audit() else ())
    if versioned:
        manifest = {
            "schema_version": 1,
            "model_version": CORRECTED_VERSION,
            "paper_data_sha256": digest(paths["bundle"]),
            "script_sha256": digest(paths["root"] / "analysis" / "figures.py"),
            "figures": {name: digest(paths["figures"] / name) for name in names},
        }
        (paths["figures"] / "figures_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versioned", action="store_true",
                        help="Read only the corrected unified bundle; write figures inside its paper directory")
    main(versioned=parser.parse_args().versioned)
