"""Agreement between the primary sidewalk audit and the blinded repeat round.

python analysis/audit_agreement.py validation/audit_results_v2.csv sidewalk_repeat_audit.csv [out.json]

Compares the sidewalk judgment only; imagery dates are not used. Presence is
compared on points judged in both rounds (neither answered "can't tell").
Reports raw agreement, Cohen's kappa and Gwet's AC1 (more stable than kappa
when agreement and prevalence are high), with percentile bootstrap intervals
(2,000 resamples, fixed seed). The interval between the two judgments of each
point is taken from the recorded first-audited timestamps and reported, with
agreement restricted to points re-judged at least 14 days later as a
sensitivity check. Agreement on the three-way judgment (both
sides / one side / none) is reported descriptively. When the same auditor did
both rounds this is intra-rater (test-retest) reliability.
"""
import csv
import datetime as dt
import json
import random
import statistics
import sys
from pathlib import Path

PRESENT = {"both", "left", "right", "present_side_unspecified"}
SEED = 20261125


def presence(value):
    if value in PRESENT:
        return 1
    if value == "none":
        return 0
    return None


def sides(value):
    return {"both": "both", "left": "one", "right": "one", "none": "none"}.get(value)


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    p1a = sum(a for a, _ in pairs) / n
    p1b = sum(b for _, b in pairs) / n
    pe = p1a * p1b + (1 - p1a) * (1 - p1b)
    k = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    pi = (p1a + p1b) / 2
    pe_ac1 = 2 * pi * (1 - pi)
    ac1 = (po - pe_ac1) / (1 - pe_ac1) if pe_ac1 < 1 else float("nan")
    return po, k, ac1


def bootstrap(pairs, reps=2000):
    rng = random.Random(SEED)
    stats = [kappa([pairs[rng.randrange(len(pairs))] for _ in pairs]) for _ in range(reps)]
    out = []
    for i in range(3):
        values = sorted(s[i] for s in stats if s[i] == s[i])
        out.append([round(values[int(0.025 * len(values))], 3),
                    round(values[int(0.975 * len(values)) - 1], 3)] if values else None)
    return out


def summarise(pairs):
    if len(pairs) < 5:
        return {"n": len(pairs)}
    po, k, ac1 = kappa(pairs)
    ci = bootstrap(pairs)
    table = {f"first_{a}_repeat_{b}": sum(1 for x, y in pairs if x == a and y == b)
             for a in (1, 0) for b in (1, 0)}
    return {"n": len(pairs), "agreement": round(po, 3), "agreement_95ci": ci[0],
            "cohen_kappa": round(k, 3), "kappa_95ci": ci[1], "gwet_ac1": round(ac1, 3),
            "ac1_95ci": ci[2], "table_1_present_0_none": table}


def main(argv):
    if len(argv) not in (3, 4):
        print(__doc__)
        return 2
    primary = {r["case_id"]: r for r in csv.DictReader(open(argv[1], encoding="utf-8-sig"))
               if r.get("round", "primary") == "primary"}
    repeat = [r for r in csv.DictReader(open(argv[2], encoding="utf-8-sig")) if r.get("completed") == "true"]
    judged, judged_14, side_pairs, gaps = [], [], [], []
    cant_tell = {"first_only": 0, "repeat_only": 0, "both": 0}
    for r in repeat:
        p = primary[r["case_id"]]
        gap = (dt.date.fromisoformat(r["first_audited_at"][:10])
               - dt.date.fromisoformat((p["first_audited_at"] or p["audit_date"])[:10])).days
        gaps.append(gap)
        a, b = presence(p["sidewalk"]), presence(r["sidewalk"])
        if a is None or b is None:
            key = "both" if a is None and b is None else "first_only" if a is None else "repeat_only"
            cant_tell[key] += 1
            continue
        judged.append((a, b))
        if gap >= 14:
            judged_14.append((a, b))
        if sides(p["sidewalk"]) and sides(r["sidewalk"]):
            side_pairs.append((sides(p["sidewalk"]), sides(r["sidewalk"])))
    result = {
        "repeat_completed": len(repeat),
        "cant_tell": cant_tell,
        "days_between_rounds": {"median": statistics.median(gaps), "min": min(gaps), "max": max(gaps),
                                "points_under_14_days": sum(g < 14 for g in gaps)} if gaps else None,
        "presence": summarise(judged),
        "presence_gap_at_least_14_days": summarise(judged_14),
        "three_way_agreement": {"n": len(side_pairs),
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
