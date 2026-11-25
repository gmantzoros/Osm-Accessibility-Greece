"""Agreement between the primary sidewalk audit and the blinded second rater.

python analysis/inter_rater.py validation/audit_results_v2.csv second_rater_XX.csv [out.json]

Presence is compared on points that both raters judged (neither answered
"can't tell"); a sensitivity restricts this to points where both used imagery
within the paper's five-year rule. Reports raw agreement, Cohen's kappa and
Gwet's AC1 (less sensitive to the high prevalence of "present"), with
percentile bootstrap intervals (2,000 resamples, fixed seed). Side-level
agreement (both / one side / none) is reported descriptively.
"""
import csv
import datetime as dt
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESENT = {"both", "left", "right", "present_side_unspecified"}
SEED = 20261125


def presence(value):
    if value in PRESENT:
        return 1
    if value == "none":
        return 0
    return None


def side(value):
    return {"both": "both", "left": "one", "right": "one", "none": "none"}.get(value)


def recent(year, reference):
    return bool(year) and year.isdigit() and int(year) >= reference.year - 5 and int(year) <= reference.year


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    p1a = sum(a for a, _ in pairs) / n
    p1b = sum(b for _, b in pairs) / n
    pe = p1a * p1b + (1 - p1a) * (1 - p1b)
    k = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    pi = (p1a + p1b) / 2
    pe_ac1 = 2 * pi * (1 - pi)
    ac1 = (po - pe_ac1) / (1 - pe_ac1)
    return po, k, ac1


def bootstrap(pairs, reps=2000):
    rng = random.Random(SEED)
    stats = []
    for _ in range(reps):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        try:
            stats.append(kappa(sample))
        except ZeroDivisionError:
            continue
    out = []
    for i in range(3):
        values = sorted(s[i] for s in stats if s[i] == s[i])
        out.append([round(values[int(0.025 * len(values))], 3), round(values[int(0.975 * len(values)) - 1], 3)])
    return out


def summarise(pairs):
    if len(pairs) < 5:
        return {"n": len(pairs)}
    po, k, ac1 = kappa(pairs)
    ci = bootstrap(pairs)
    table = {f"primary_{a}_second_{b}": sum(1 for x, y in pairs if x == a and y == b)
             for a in (1, 0) for b in (1, 0)}
    return {"n": len(pairs), "agreement": round(po, 3), "agreement_95ci": ci[0],
            "cohen_kappa": round(k, 3), "kappa_95ci": ci[1], "gwet_ac1": round(ac1, 3),
            "ac1_95ci": ci[2], "table": table}


def main(argv):
    if len(argv) not in (3, 4):
        print(__doc__)
        return 2
    meta = json.loads((ROOT / "results/impact/unified-conditional-2026-10-09/analysis/local_audit.json")
                      .read_text(encoding="utf-8"))
    reference = dt.date.fromisoformat(meta["reference_date"])
    primary = {r["case_id"]: r for r in csv.DictReader(open(argv[1], encoding="utf-8-sig"))
               if r.get("round", "primary") == "primary"}
    second = [r for r in csv.DictReader(open(argv[2], encoding="utf-8-sig")) if r.get("completed") == "true"]
    raters = sorted({r["rater"] for r in second})
    judged, judged_recent, sides, unverifiable = [], [], [], {"primary_only": 0, "second_only": 0, "both": 0}
    for r in second:
        p = primary[r["case_id"]]
        a, b = presence(p["sidewalk"]), presence(r["sidewalk"])
        if a is None or b is None:
            key = "both" if a is None and b is None else "primary_only" if a is None else "second_only"
            unverifiable[key] += 1
            continue
        judged.append((a, b))
        if recent(p["capture_date"][:4], reference) and recent(r["capture_date"][:4], reference):
            judged_recent.append((a, b))
        sides.append((side(p["sidewalk"]), side(r["sidewalk"])))
    side_pairs = [(x, y) for x, y in sides if x and y]
    result = {
        "raters": raters, "second_rater_completed": len(second),
        "cant_tell": unverifiable,
        "presence_all_judged": summarise(judged),
        "presence_both_recent_imagery": summarise(judged_recent),
        "side_agreement": {"n": len(side_pairs),
                           "exact": round(sum(x == y for x, y in side_pairs) / len(side_pairs), 3)
                           if side_pairs else None},
    }
    text = json.dumps(result, indent=1)
    if len(argv) == 4:
        Path(argv[3]).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
