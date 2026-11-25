"""Sidewalk-presence model fitted on the 500-point imagery audit.

Fits a penalised logistic model of sidewalk presence at sampled points on
drivable ways without a sidewalk key, with road-class group and city-proxy
fixed effects and a community random intercept (fitted as a ridge penalty).
It writes point and bootstrap probability tables per (community, road-class
group) that census/CalibrationMain uses to impute sidewalk states on the
untagged roads of the 50 audited communities.

The model describes the audited frame only. Probabilities apply to whole
ways although the audit observed points; this assumption is reported with
the results, together with the continuity pilot that bears on it.

Usage (needs numpy):
    python analysis/sidewalk_calibration_model.py \
        validation/audit_results_v2.csv results/calibration/sidewalk_model.json
"""
import csv
import datetime as dt
import json
import random
import re
import sys
from pathlib import Path

import numpy as np  # linear algebra only; sampling uses the standard library

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import local_audit  # noqa: E402  (same repository, provides the paper's outcome rule)

GROUPS = ["residential", "primary", "secondary_tertiary", "service_unclassified"]
EXCLUDED = {"motorway", "motorway_link", "trunk", "trunk_link"}
LOCAL_AUDIT_JSON = ROOT / "results/impact/unified-conditional-2026-10-09/analysis/local_audit.json"
SEED = 20261125
R_MAIN = 200
R_VARIANT = 100


def road_group(highway):
    if highway in ("primary", "primary_link"):
        return "primary"
    if highway in ("secondary", "secondary_link", "tertiary", "tertiary_link"):
        return "secondary_tertiary"
    if highway in ("residential", "living_street", "road"):
        return "residential"
    return "service_unclassified"


def load(csv_path):
    meta = json.loads(LOCAL_AUDIT_JSON.read_text(encoding="utf-8"))
    reference = dt.date.fromisoformat(meta["reference_date"])
    city_groups = meta["city_groups"]
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    records = []
    for row in rows:
        if row.get("round", "primary") not in ("primary", "", "1"):
            continue
        match = re.search(r"on this (\S+) \(way", row["osm_assertion"])
        if not match:
            raise ValueError("Cannot parse road class: " + row["osm_assertion"])
        highway = match.group(1)
        records.append({
            "case_id": row["case_id"],
            "community": row["stratum"],
            "highway": highway,
            "group": road_group(highway),
            "eligible": highway not in EXCLUDED,
            "city": city_groups[row["stratum"]] == "proxy_cities",
            "y": local_audit.outcome(row, "sidewalk_present", reference),
        })
    return records, sorted(city_groups), city_groups, reference


class Design:
    """Columns: intercept, 3 class dummies, city, one column per community."""

    def __init__(self, communities):
        self.communities = communities
        self.index = {c: i for i, c in enumerate(communities)}
        self.k_fixed = 1 + (len(GROUPS) - 1) + 1
        self.k = self.k_fixed + len(communities)

    def row(self, group, city, community):
        x = np.zeros(self.k)
        x[0] = 1.0
        if group != GROUPS[0]:
            x[GROUPS.index(group)] = 1.0
        x[len(GROUPS)] = 1.0 if city else 0.0
        x[self.k_fixed + self.index[community]] = 1.0
        return x

    def matrix(self, recs):
        return np.array([self.row(r["group"], r["city"], r["community"]) for r in recs])


def fit(X, y, design, sigma, use_city=True, use_class=True):
    prior = np.full(design.k, 2.5)
    prior[0] = 5.0
    prior[design.k_fixed:] = sigma if sigma > 0 else 1e-6
    mask = np.ones(design.k)
    if not use_class:
        mask[1:len(GROUPS)] = 0
    if not use_city:
        mask[len(GROUPS)] = 0
    if sigma <= 0:
        mask[design.k_fixed:] = 0
    Xm = X * mask
    inv_var = 1.0 / prior ** 2
    beta = np.zeros(design.k)
    # Penalised Newton-Raphson (MAP estimate); the objective is strictly convex.
    for _ in range(100):
        p = 1 / (1 + np.exp(-(Xm @ beta)))
        grad = Xm.T @ (y - p) - inv_var * beta
        hess = (Xm * (p * (1 - p))[:, None]).T @ Xm + np.diag(inv_var)
        step = np.linalg.solve(hess, grad)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return beta * mask


def predict(beta, X):
    return 1 / (1 + np.exp(-(X @ beta)))


def auc(y, p):
    order = np.argsort(p)
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    # average ties
    for value in np.unique(p):
        tie = p == value
        if tie.sum() > 1:
            ranks[tie] = ranks[tie].mean()
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def cross_validate(recs, design, spec, rng, folds=10, repeats=5):
    X = design.matrix(recs)
    y = np.array([r["y"] for r in recs], dtype=float)
    preds = []
    for _ in range(repeats):
        order = list(range(len(recs)))
        rng.shuffle(order)
        fold = np.empty(len(recs), dtype=int)
        fold[order] = np.arange(len(recs)) % folds
        p = np.empty(len(recs))
        for f in range(folds):
            train, test = fold != f, fold == f
            if spec == "overall":
                p[test] = y[train].mean()
            else:
                beta = fit(X[train], y[train], design, spec["sigma"],
                           use_city=spec["city"], use_class=spec["class"])
                p[test] = predict(beta, X[test])
        preds.append(p)
    out = {"log_loss": [], "brier": [], "auc": []}
    for p in preds:
        q = np.clip(p, 1e-6, 1 - 1e-6)
        out["log_loss"].append(float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))))
        out["brier"].append(float(np.mean((p - y) ** 2)))
        out["auc"].append(auc(y, p))
    return {k: round(float(np.mean(v)), 4) for k, v in out.items()}


def table(beta, design, city_groups):
    cells = {}
    for community in design.communities:
        city = city_groups[community] == "proxy_cities"
        cells[community] = {g: round(float(predict(beta, design.row(g, city, community)[None, :])[0]), 6)
                            for g in GROUPS}
    return cells


def outcome_variant(recs, variant):
    out = []
    for r in recs:
        y = r["y"]
        if y is None:
            if variant == "mar":
                continue
            y = 0 if variant == "unresolved_absent" else 1
        out.append({**r, "y": y})
    return out


def bootstrap(all_recs, design, city_groups, sigma, variant, reps, rng):
    by_comm = {}
    for r in all_recs:
        by_comm.setdefault(r["community"], []).append(r)
    tables = []
    for _ in range(reps):
        sample = []
        for community in sorted(by_comm):
            rows = by_comm[community]
            sample.extend(rows[rng.randrange(len(rows))] for _ in rows)
        recs = outcome_variant(sample, variant)
        X = design.matrix(recs)
        y = np.array([r["y"] for r in recs], dtype=float)
        tables.append(table(fit(X, y, design, sigma), design, city_groups))
    return tables


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    rng = random.Random(SEED)
    records, communities, city_groups, reference = load(argv[1])
    eligible = [r for r in records if r["eligible"]]
    design = Design(communities)
    judged = outcome_variant(eligible, "mar")

    specs = {
        "overall_rate": "overall",
        "class_only": {"sigma": 0, "city": False, "class": True},
        "class_city": {"sigma": 0, "city": True, "class": True},
    }
    for s in (0.25, 0.5, 0.75, 1.0, 1.5):
        specs[f"class_city_community_sigma{s}"] = {"sigma": s, "city": True, "class": True}
    cv = {name: cross_validate(judged, design, spec, random.Random(SEED))
          for name, spec in specs.items()}
    community_specs = [k for k in cv if k.startswith("class_city_community")]
    best = min(community_specs, key=lambda k: cv[k]["log_loss"])
    sigma = specs[best]["sigma"]

    X = design.matrix(judged)
    y = np.array([r["y"] for r in judged], dtype=float)
    beta = fit(X, y, design, sigma)
    fixed_names = ["intercept"] + [f"class:{g}" for g in GROUPS[1:]] + ["city_proxy"]
    coefficients = {name: round(float(b), 4) for name, b in zip(fixed_names, beta[:design.k_fixed])}
    community_effects = {c: round(float(b), 4) for c, b in zip(communities, beta[design.k_fixed:])}

    observed = {}
    for g in GROUPS:
        ys = [r["y"] for r in judged if r["group"] == g]
        observed[g] = {"judged": len(ys), "present": int(sum(ys)),
                       "share": round(sum(ys) / len(ys), 4) if ys else None}

    variants = {}
    for variant, reps in (("mar", R_MAIN), ("unresolved_absent", R_VARIANT),
                          ("unresolved_present", R_VARIANT)):
        recs = outcome_variant(eligible, variant)
        Xv = design.matrix(recs)
        yv = np.array([r["y"] for r in recs], dtype=float)
        point = table(fit(Xv, yv, design, sigma), design, city_groups)
        variants[variant] = {
            "n_used": len(recs),
            "point": point,
            "replicates": bootstrap(eligible, design, city_groups, sigma, variant, reps, rng),
        }

    out = {
        "schema_version": 1,
        "description": "Penalised logistic model of sidewalk presence at audited points on "
                       "drivable ways without a sidewalk key; probabilities per community and "
                       "road-class group for whole-way imputation in census CalibrationMain.",
        "audit_reference_date": reference.isoformat(),
        "seed": SEED,
        "groups": GROUPS,
        "group_definition": {
            "primary": ["primary", "primary_link"],
            "secondary_tertiary": ["secondary", "secondary_link", "tertiary", "tertiary_link"],
            "residential": ["residential", "living_street", "road"],
            "service_unclassified": ["service", "unclassified", "other drivable"],
        },
        "excluded_from_fit": sorted(EXCLUDED),
        "n_points_total": len(records),
        "n_points_eligible_classes": len(eligible),
        "n_judged_eligible_classes": len(judged),
        "observed_by_group": observed,
        "cross_validation": {"folds": 10, "repeats": 5, "results": cv, "selected": best},
        "community_sigma": sigma,
        "fixed_effects_logit": coefficients,
        "community_effects_logit": community_effects,
        "city_groups": city_groups,
        "variants": variants,
    }
    Path(argv[2]).parent.mkdir(parents=True, exist_ok=True)
    Path(argv[2]).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {k: out[k] for k in ("n_judged_eligible_classes", "observed_by_group",
                                    "community_sigma", "fixed_effects_logit")}
    summary["cross_validation"] = cv
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
