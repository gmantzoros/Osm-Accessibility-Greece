"""Figures for the audit-calibrated scenario and the tagging trend (house style of figures.py).

Usage: python analysis/figures_strengthening.py paper/figures
"""
import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE, HI, GREEN, MUTE, FAINT = "#2E4A66", "#C1611F", "#3F7350", "#9AA5B1", "#D8DEE5"
DPI, W = 300, 6.2
plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"], "font.size": 8.5,
    "axes.edgecolor": "#5A6B7C", "axes.labelcolor": "#28323C", "text.color": "#28323C",
    "xtick.color": "#5A6B7C", "ytick.color": "#5A6B7C", "axes.spines.top": False,
    "axes.spines.right": False, "figure.dpi": DPI, "savefig.dpi": DPI,
})


def calibration(out):
    rows = list(csv.DictReader(open(ROOT / "results/calibration/calibration_by_community.csv", encoding="utf-8")))
    rows.sort(key=lambda r: float(r["C1_mean"]))
    summary = json.loads((ROOT / "results/calibration/calibration_summary.json").read_text())
    fig, ax = plt.subplots(figsize=(W, 7.4))
    ys = range(len(rows))
    for y, r in zip(ys, rows):
        p, o = float(r["pessimistic"]), float(r["optimistic"])
        ax.plot([p, o], [y, y], color=FAINT, lw=1.2, zorder=1)
        ax.plot([float(r["C1_lo"]), float(r["C1_hi"])], [y, y], color=HI, lw=2.4, alpha=0.55,
                solid_capstyle="round", zorder=2)
    ax.scatter([float(r["pessimistic"]) for r in rows], ys, s=16, color=BASE, zorder=3, label="Pessimistic")
    ax.scatter([float(r["optimistic"]) for r in rows], ys, s=16, facecolor="white", edgecolor=BASE,
               lw=1.1, zorder=3, label="Optimistic")
    ax.scatter([float(r["C1_mean"]) for r in rows], ys, s=20, color=HI, zorder=4,
               label="Audit-calibrated (C1), mean and 95% interval")
    ax.set_yticks(list(ys))
    ax.set_yticklabels([r["label"] for r in rows], fontsize=6.8)
    ax.set_ylim(-1, len(rows))
    ax.set_xlim(0, 100)
    ax.grid(axis="x", color=FAINT, lw=0.6)
    ax.set_axisbelow(True)
    ax.set_xlabel("Usable share of eligible length (%)", fontsize=8.2)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.06), ncol=3, frameon=False, fontsize=7.4,
              handletextpad=0.4, columnspacing=1.2)
    fig.tight_layout()
    fig.savefig(out / "fig10_calibrated_scenario.png", dpi=DPI)
    plt.close(fig)


def trend(out):
    rows = list(csv.DictReader(open(ROOT / "results/trend/tagging_trend.csv", encoding="utf-8")))
    year = [int(r["date"][:4]) + (0.5 if r["date"][5:7] == "07" else 0) for r in rows]
    f = lambda k: [float(r[k]) for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(W, 2.6))
    a = axes[0]
    a.plot(year, f("crossing_nodes_tactile_stated"), color=BASE, lw=1.6, label="Crossing nodes: tactile tag")
    a.plot(year, f("crossing_nodes_tactile_yes"), color=BASE, lw=1.6, ls="--", label="  of which affirmative")
    a.plot(year, f("signal_nodes_sound_stated"), color=HI, lw=1.6, label="Signal nodes: sound tag")
    a.plot(year, f("signal_nodes_sound_yes"), color=HI, lw=1.6, ls="--", label="  of which affirmative")
    a.set_ylabel("OSM records", fontsize=8)
    a.legend(frameon=False, fontsize=6.6, loc="upper left")
    a.text(-0.2, 1.06, "A", transform=a.transAxes, fontsize=10, fontweight="bold")
    b = axes[1]
    share = [100 * s / d for s, d in zip(f("drivable_km_sidewalk_stated"), f("drivable_km"))]
    b.plot(year, share, color=GREEN, lw=1.8)
    b.set_ylabel("Drivable length with sidewalk tag (%)", fontsize=8)
    b.set_ylim(0, max(share) * 1.25)
    b.text(year[-1], share[-1], f" {share[-1]:.1f}%", va="center", fontsize=7.4, color=GREEN)
    b.text(-0.22, 1.06, "B", transform=b.transAxes, fontsize=10, fontweight="bold")
    for ax in axes:
        ax.set_xlim(2012, 2027)
        ax.set_xticks([2012, 2016, 2020, 2024])
        ax.grid(axis="y", color=FAINT, lw=0.6)
        ax.set_axisbelow(True)
    fig.tight_layout(w_pad=2.5)
    fig.savefig(out / "fig11_tagging_trend.png", dpi=DPI)
    plt.close(fig)


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "paper/figures"
    out.mkdir(parents=True, exist_ok=True)
    calibration(out)
    trend(out)
    print("wrote", out / "fig10_calibrated_scenario.png", out / "fig11_tagging_trend.png")


if __name__ == "__main__":
    main(sys.argv)
