#!/usr/bin/env python3
"""Analyse schema-v2 local-feature audits, never routes or national campaign yields.

python -B analysis/local_audit.py validation/audit_results_v2.csv --reference-date 2026-09-15
Exports must contain every canonical primary case, including unfinished cases.
Only stdout is written unless --output is supplied. The 95% intervals are
conservative stratified Hoeffding intervals around missing-outcome bounds.
"""
import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = ROOT / "results" / "census" / "audit_sample.csv"
DEFAULT_INPUT = ROOT / "data" / "greece-latest.osm.pbf"
SCHEMA = "local-feature-audit-v2"
OUTCOMES = {
    "crossing_exists": ["present", "absent", "unverifiable"],
    "markings": ["marked", "unmarked", "unverifiable", "not_applicable"],
    "tactile": ["present", "absent", "unverifiable", "not_applicable"],
    "kerb": ["lowered", "flush", "raised", "other", "unverifiable", "not_applicable"],
    "sidewalk": ["both", "left", "right", "present_side_unspecified", "none", "unverifiable"],
}
IDENTITY = ["case_id", "group", "stratum", "arm", "lat", "lon", "feature_id",
            "osm_assertion", "mapped_feature_state", "pool_n", "draw_n",
            "inclusion_probability", "radius_m", "sampling_frame", "pool_sha256"]
IDENTITY.append("seed")
FIELDS = ["schema_version", "sample_fingerprint", "input_sha256", "round"] + IDENTITY + [
    "source", "source_reference", "capture_date", "audit_date", "unverifiable_reason",
    *OUTCOMES, "notes", "completed", "first_audited_at", "updated_at"]


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(cases, input_sha256, sample_sha256):
    value = {"schema": SCHEMA, "outcomes": OUTCOMES, "export_fields": FIELDS,
             "input_sha256": input_sha256,
             "sample_sha256": sample_sha256,
             "cases": [{k: c.get(k, "") for k in IDENTITY} for c in cases]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def repeat_ids(cases, fp):
    """Outcome-independent deterministic random 10% (currently 50 of 500)."""
    n = round(len(cases) * .1)
    # A simple seeded random 10% of all cases; independent of human outcomes.
    return sorted((c["case_id"] for c in cases),
                  key=lambda x: hashlib.sha256((fp + "|repeat|" + x).encode()).digest())[:n]


def capture_end(value):
    if len(value) == 4:
        return dt.date(int(value), 12, 31)
    return dt.date.fromisoformat(value)


def recent(row, reference, years=5):
    try:
        date = capture_end(row["capture_date"])
        start = dt.date(date.year, 1, 1) if len(row["capture_date"]) == 4 else date
        try:
            cutoff = reference.replace(year=reference.year-years)
        except ValueError:
            cutoff = reference.replace(year=reference.year-years, day=28)
        return start >= cutoff and start <= reference and (
            len(row["capture_date"]) == 4 or date <= reference)
    except (ValueError, KeyError):
        return False


def _sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _positive_integer(value, field):
    try:
        number = int(value)
        if str(number) != value or number < 1:
            raise ValueError()
        return number
    except (TypeError, ValueError):
        raise ValueError("Invalid probability metadata: " + field) from None


def _expected_frame(cases, manifest):
    if not cases or not isinstance(manifest, dict):
        raise ValueError("Canonical expected cases and manifest are required")
    if (manifest.get("schema_version") != SCHEMA
            or manifest.get("identity_fields") != IDENTITY
            or manifest.get("schema_fields") != FIELDS
            or manifest.get("outcomes") != OUTCOMES):
        raise ValueError("Canonical manifest schema mismatch")
    for field in ("sample_fingerprint", "input_sha256", "sample_sha256"):
        if not _sha256(manifest.get(field)):
            raise ValueError("Invalid canonical SHA-256: " + field)
    index, cells = {}, defaultdict(list)
    for case in cases:
        if any(not isinstance(case.get(k), str) for k in IDENTITY):
            raise ValueError("Incomplete canonical case identity")
        case_id = case["case_id"]
        if not case_id or case_id in index:
            raise ValueError("Missing/duplicate canonical case identity: " + case_id)
        if not case["stratum"] or case["arm"] not in ("sidewalk", "commission", "omission"):
            raise ValueError("Invalid canonical city/arm allocation")
        if not _sha256(case["pool_sha256"]):
            raise ValueError("Invalid probability metadata: pool_sha256")
        if not case["sampling_frame"]:
            raise ValueError("Invalid probability metadata: sampling_frame")
        index[case_id] = case
        cells[case["arm"], case["stratum"]].append(case)
    if (type(manifest.get("cases")) is not int or manifest["cases"] != len(cases)
            or type(manifest.get("cities")) is not int
            or manifest["cities"] != len({c["stratum"] for c in cases})
            or manifest.get("sampling_frames") != sorted({c["sampling_frame"] for c in cases})):
        raise ValueError("Canonical manifest allocation mismatch")
    if manifest["sample_fingerprint"] != fingerprint(
            cases, manifest["input_sha256"], manifest["sample_sha256"]):
        raise ValueError("Canonical sample fingerprint mismatch")
    if manifest.get("repeat_ids") != repeat_ids(cases, manifest["sample_fingerprint"]):
        raise ValueError("Canonical deterministic repeat selection mismatch")
    pools = {}
    for cell, members in cells.items():
        pool_sizes = {_positive_integer(c["pool_n"], "pool_n") for c in members}
        draw_sizes = {_positive_integer(c["draw_n"], "draw_n") for c in members}
        if len(pool_sizes) != 1 or draw_sizes != {len(members)}:
            raise ValueError("Canonical probability metadata/allocation mismatch: " + str(cell))
        pool = next(iter(pool_sizes))
        if pool < len(members):
            raise ValueError("Canonical draw exceeds its pool: " + str(cell))
        for field in ("group", "pool_sha256", "sampling_frame", "seed"):
            if len({c[field] for c in members}) != 1:
                raise ValueError("Inconsistent canonical " + field + ": " + str(cell))
        for case in members:
            try:
                pi = float(case["inclusion_probability"])
            except (TypeError, ValueError):
                raise ValueError("Invalid probability metadata: inclusion_probability") from None
            if (not math.isfinite(pi) or not 0 < pi <= 1
                    or not math.isclose(pi, len(members)/pool, rel_tol=0., abs_tol=1e-9)):
                raise ValueError("Invalid probability metadata: inclusion_probability")
        pools[cell] = pool
    return index, cells, pools


def _validate_seed_metadata(sample_path, cases, manifest, pools):
    seed_path = Path(sample_path).with_name("audit_sample_seed.txt")
    if not seed_path.is_file():
        return
    values = dict(line.split("=", 1)
                  for line in seed_path.read_text(encoding="utf-8-sig").splitlines()
                  if "=" in line)
    allocations = {
        "cases_written": len(cases),
        "scope_strata": manifest["cities"],
    }
    for arm in {c["arm"] for c in cases}:
        allocations[arm + "_pool"] = sum(n for (a, _), n in pools.items() if a == arm)
    for field, expected in allocations.items():
        if field in values and _positive_integer(values[field], field) != expected:
            raise ValueError("Canonical seed metadata allocation mismatch: " + field)
    if "per_group_per_arm" in values:
        draw = _positive_integer(values["per_group_per_arm"], "per_group_per_arm")
        if any(int(c["draw_n"]) != draw for c in cases):
            raise ValueError("Canonical seed metadata allocation mismatch: per_group_per_arm")
    for field in ("seed", "sampling_frame", "pool_sha256"):
        if field in values and any(c[field] != values[field] for c in cases):
            raise ValueError("Canonical seed metadata mismatch: " + field)
    if "input_sha256" in values and values["input_sha256"] != manifest["input_sha256"]:
        raise ValueError("Canonical seed metadata mismatch: input_sha256")


def load_expected_sample(sample_path=DEFAULT_SAMPLE, input_path=DEFAULT_INPUT):
    """Return (cases, manifest) using the protected builder's read-only prepare().

    Hashes the supplied sample and immutable input once each; does not generate
    HTML, access browser storage, or change the sample. Callers may reuse this
    pair for multiple exports without rehashing the input.
    """
    for field, path in (("sample", sample_path), ("input", input_path)):
        if not Path(path).is_file():
            raise ValueError("Canonical " + field + " file is unavailable: " + str(path))
    spec = importlib.util.spec_from_file_location(
        "_local_audit_canonical_builder", ROOT / "validation" / "build_harness.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    try:
        cases, manifest = builder.prepare(sample_path, input_path)
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid canonical sample schema/metadata: " + str(error)) from None
    _, _, pools = _expected_frame(cases, manifest)
    _validate_seed_metadata(sample_path, cases, manifest, pools)
    return cases, manifest


def _audit_date(value):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Invalid completed audit_date") from None


def _timestamp(value, field):
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            raise ValueError()
        return stamp.astimezone(dt.timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Invalid/timezone-inconsistent " + field
                         + "; repeat timing requires timezone-aware ISO timestamps") from None


def validate_rows(rows, *, expected_cases=None, manifest=None):
    if not rows:
        raise ValueError("No audit rows")
    required = {"schema_version", "sample_fingerprint", "input_sha256", "round", *IDENTITY,
                "completed", *OUTCOMES}
    if any(not required.issubset(r) for r in rows):
        raise ValueError("Legacy/incomplete schema: do not silently migrate historical answers")
    expected, cells, pools = _expected_frame(expected_cases, manifest)
    seen, primary_ids = set(), set()
    allowed_repeats = set(manifest["repeat_ids"])
    for r in rows:
        if r["schema_version"] != SCHEMA:
            raise ValueError("Unsupported audit schema")
        for field in ("sample_fingerprint", "input_sha256"):
            if not _sha256(r[field]):
                raise ValueError("Invalid SHA-256 identity: " + field)
            if r[field] != manifest[field]:
                raise ValueError("Audit/canonical " + field + " mismatch")
        case = expected.get(r["case_id"])
        if case is None:
            raise ValueError("Unknown canonical case: " + str(r["case_id"]))
        for field in IDENTITY:
            if r[field] != case[field]:
                raise ValueError("Immutable case identity mismatch: " + r["case_id"] + "/" + field)
        key = (r["round"], r["case_id"])
        if key in seen:
            raise ValueError("Duplicate round/case: " + str(key))
        seen.add(key)
        if r["round"] not in ("primary", "repeat"):
            raise ValueError("Unknown audit round")
        if r["round"] == "primary":
            primary_ids.add(r["case_id"])
        elif r["case_id"] not in allowed_repeats:
            raise ValueError("Not a deterministically selected repeat case: " + r["case_id"])
        if r["completed"] not in ("", "false", "true"):
            raise ValueError("Invalid completion flag; use blank, false or true")
        for k, allowed in OUTCOMES.items():
            if r[k] and r[k] not in allowed:
                raise ValueError("Invalid " + k + " outcome")
        if r.get("completed") == "true":
            if not r.get("source") or not r.get("audit_date"):
                raise ValueError("Completed observations require source and audit date")
            _audit_date(r["audit_date"])
            relevant = ["sidewalk"] if r["arm"] == "sidewalk" else [
                "crossing_exists", "markings", "tactile", "kerb"]
            if any(not r[k] for k in relevant):
                raise ValueError("Completed observations have missing independent outcomes")
            if any(r[k] == "unverifiable" for k in relevant) and not r.get("unverifiable_reason"):
                raise ValueError("Unverifiable observations require a reason")
    missing = set(expected) - primary_ids
    if missing:
        raise ValueError("Missing canonical primary cases (" + str(len(missing))
                         + "): " + ", ".join(sorted(missing)[:10])
                         + "; retain unfinished rows as UNKNOWN")
    repeats = [r for r in rows if r["round"] == "repeat" and r.get("completed") == "true"]
    if repeats:
        primary = [r for r in rows if r["round"] == "primary"]
        if any(r.get("completed") != "true" for r in primary):
            raise ValueError("Repeat analysis requires the complete primary export")
        finished = max(_timestamp(r.get("updated_at"), "primary updated_at") for r in primary)
        starts = [_timestamp(r.get("first_audited_at"), "repeat first_audited_at")
                  for r in repeats]
        if any(start < finished + dt.timedelta(days=14) for start in starts):
            raise ValueError("Repeat observations precede the fourteen-day separation")
        for row, start in zip(repeats, starts):
            if row.get("updated_at") and _timestamp(
                    row["updated_at"], "repeat updated_at") < start:
                raise ValueError("Repeat updated_at precedes first_audited_at")
    return cells, pools


def capture_status(row, reference, years=5):
    """Explain the unchanged recent() rule, including year-only cutoff ambiguity."""
    value = row.get("capture_date", "")
    if not value:
        return "undated"
    try:
        date = capture_end(value)
        start = dt.date(date.year, 1, 1) if len(value) == 4 else date
        try:
            cutoff = reference.replace(year=reference.year-years)
        except ValueError:
            cutoff = reference.replace(year=reference.year-years, day=28)
    except (TypeError, ValueError):
        return "invalid"
    if start > reference:
        return "future"
    if len(value) == 4 and start < cutoff <= date:
        return "ambiguous_cutoff"
    if date < cutoff:
        return "stale"
    return "recent" if recent(row, reference, years) else "invalid"


def _quality_counts(rows, reference):
    counts = {k: 0 for k in ("completed", "incomplete", "recent", "undated", "invalid",
                            "stale", "future", "ambiguous_cutoff", "year_only",
                            "year_only_current_year_ambiguity", "missing_source_reference")}
    for row in rows:
        counts["completed" if row.get("completed") == "true" else "incomplete"] += 1
        status = capture_status(row, reference)
        counts[status] += 1
        value = row.get("capture_date", "")
        if isinstance(value, str) and len(value) == 4 and status != "invalid":
            counts["year_only"] += 1
            counts["year_only_current_year_ambiguity"] += value == str(reference.year)
        counts["missing_source_reference"] += not bool(row.get("source_reference"))
    return counts


def outcome(row, metric, reference):
    if row.get("completed") != "true" or capture_status(row, reference) != "recent":
        return None
    if metric == "sidewalk_present":
        v = row.get("sidewalk", "")
        return 1 if v in ("both", "left", "right", "present_side_unspecified") else 0 if v == "none" else None
    exists = row.get("crossing_exists", "")
    if exists not in ("present", "absent"):
        return None
    if metric == "crossing_present" or exists == "absent":
        return int(exists == "present") if metric == "crossing_present" else 0
    field, positives, negatives = {
        "marked_crossing_present": ("markings", {"marked"}, {"unmarked"}),
        "tactile_crossing_present": ("tactile", {"present"}, {"absent"}),
        "lowered_or_flush_crossing_kerb_present":
            ("kerb", {"lowered", "flush"}, {"raised", "other"}),
    }[metric]
    v = row.get(field, "")
    return 1 if v in positives else 0 if v in negatives else None


def estimate(strata, weights, pools=None, alpha=.05):
    """Fixed-city stratified SRS; absent outcomes are bounded, never dropped."""
    lower = upper = variance_lower = variance_upper = variance_bound = 0.
    observed = observed_positive = 0.
    for city, ys in strata.items():
        n = len(ys)
        if n < 1:
            raise ValueError("Empty sampling stratum")
        w = weights[city]
        lo = [0 if y is None else y for y in ys]
        hi = [1 if y is None else y for y in ys]
        lower += w * sum(lo) / n
        upper += w * sum(hi) / n
        observed += w * sum(y is not None for y in ys) / n
        observed_positive += w * sum(y == 1 for y in ys) / n
        fpc = 1 - n / pools[city] if pools else 1
        if n > 1:
            variance_lower += w*w * fpc * sum((x-sum(lo)/n)**2 for x in lo)/(n*(n-1))
            variance_upper += w*w * fpc * sum((x-sum(hi)/n)**2 for x in hi)/(n*(n-1))
        variance_bound += w*w/n
    epsilon = math.sqrt(math.log(2/alpha) * variance_bound / 2)
    return {"missing_bounds": [lower, upper],
            "conservative_95pct_interval": [max(0., lower-epsilon), min(1., upper+epsilon)],
            "verified_only_descriptive_rate": observed_positive/observed if observed else None,
            "weighted_verifiable_fraction": observed,
            "endpoint_design_se": [math.sqrt(variance_lower), math.sqrt(variance_upper)]}


def _reference_date(rows, reference):
    if reference is None:
        dates = [_audit_date(r.get("audit_date")) for r in rows
                 if r["round"] == "primary" and r.get("completed") == "true"]
        if not dates:
            raise ValueError("An explicit reference date is required without completed primary audits")
        return max(dates), "latest_completed_primary_audit_date"
    if isinstance(reference, str):
        try:
            reference = dt.date.fromisoformat(reference)
        except ValueError:
            raise ValueError("Invalid reference date; use YYYY-MM-DD") from None
    if not isinstance(reference, dt.date) or isinstance(reference, dt.datetime):
        raise ValueError("Reference date must be a date or YYYY-MM-DD")
    return reference, "explicit"


def _missing_reason(row, metric, reference):
    if row.get("completed") != "true":
        return "incomplete"
    status = capture_status(row, reference)
    if status != "recent":
        return status
    if metric == "sidewalk_present":
        value = row.get("sidewalk", "")
    elif row.get("crossing_exists") not in ("present", "absent"):
        value = row.get("crossing_exists", "")
    else:
        field = {"crossing_present": "crossing_exists",
                 "marked_crossing_present": "markings",
                 "tactile_crossing_present": "tactile",
                 "lowered_or_flush_crossing_kerb_present": "kerb"}[metric]
        value = row.get(field, "")
    return value if value in ("unverifiable", "not_applicable") else "missing_outcome"


def analyse(rows, reference=None, *, expected_cases, manifest):
    """Analyse a full canonical primary export, retaining UNKNOWN denominators.

    Obtain expected_cases and manifest from load_expected_sample(), not from the
    submitted answers. If reference is omitted, use the latest completed primary
    audit_date, never today's date or a filesystem timestamp.
    """
    _, frame_pools = validate_rows(rows, expected_cases=expected_cases, manifest=manifest)
    reference, reference_source = _reference_date(rows, reference)
    primary = [r for r in rows if r["round"] == "primary"]
    repeats = [r for r in rows if r["round"] == "repeat"]
    completed_primary = sum(r.get("completed") == "true" for r in primary)
    completed_repeats = sum(r.get("completed") == "true" for r in repeats)
    expected_repeats = len(manifest["repeat_ids"])
    full_primary = completed_primary == len(expected_cases)
    full_repeat = len(repeats) == completed_repeats == expected_repeats
    cells = defaultdict(list)
    for r in primary:
        cells[r["arm"], r["stratum"]].append(r)
    result = {"schema_version": SCHEMA, "reference_date": reference.isoformat(),
              "reference_date_source": reference_source,
              "sample_fingerprint": manifest["sample_fingerprint"],
              "sample_sha256": manifest["sample_sha256"],
              "input_sha256": manifest["input_sha256"],
              "sampling_frames": manifest["sampling_frames"],
              "expected_primary_count": len(expected_cases),
              "supplied_primary_count": len(primary),
              "completed_primary_count": completed_primary,
              "incomplete_primary_count": len(primary) - completed_primary,
              "expected_repeat_count": expected_repeats,
              "expected_repeat_ids": manifest["repeat_ids"],
              "supplied_repeat_count": len(repeats),
              "completed_repeat_count": completed_repeats,
              "incomplete_repeat_count": len(repeats) - completed_repeats,
              "missing_repeat_count": expected_repeats - len(repeats),
              "full_primary": full_primary,
              "full_repeat": full_repeat,
              "repeat_status": ("not_required" if not expected_repeats else
                                "complete" if full_repeat else
                                "incomplete" if repeats else "not_supplied"),
              "scope": "Equal-city and pool-weighted estimates for sampled local marker/way "
                       "locations within the selected cities and historical sampling frame",
              "warning": "No national, length-weighted, graph, route, acoustic-function, "
                         "campaign-yield, cross-country or ground-truth accuracy inference",
              "source_quality_limitations": [
                  "Source/viewer links do not prove the identity or capture date of the image "
                  "actually inspected; source and capture metadata are observer supplied.",
                  "Year-only capture dates cannot locate an observation within the year. "
                  "A year straddling the five-year cutoff is UNKNOWN; a current-year date "
                  "retains the existing recent() rule but may postdate the reference date.",
                  "Incomplete, undated, invalid, stale, future, ambiguous-cutoff and "
                  "unverifiable outcomes remain in the full sampled denominator.",
                  "Repeat agreement is descriptive within-observer consistency, not "
                  "ground-truth accuracy; an incomplete repeat round is not full reliability.",
                  "Pool weights represent the sampled local marker/way frame, not street "
                  "length, population, all national features or functional network truth.",
              ],
              "quality_counts": {"primary": _quality_counts(primary, reference),
                                 "repeat": _quality_counts(repeats, reference)},
              "quality_count_note": "Capture-status counts are mutually exclusive; completion, "
                                    "year-only and source-reference counts overlap them.",
              "missing_reason_definitions": {
                  "incomplete": "Completion flag is blank or false.",
                  "undated": "No capture date supplied.",
                  "invalid": "Capture date cannot be parsed.",
                  "stale": "Capture date precedes the five-year window.",
                  "future": "Capture date (or year start) is after the reference date.",
                  "ambiguous_cutoff": "Year-only capture interval straddles the five-year cutoff.",
                  "unverifiable": "Observer explicitly could not verify the relevant outcome.",
                  "not_applicable": "Attribute does not supply a verifiable binary outcome.",
                  "missing_outcome": "No usable independent outcome.",
              },
              "rates": [], "city_arm_counts": []}
    for (arm, city), rs in sorted(cells.items()):
        result["city_arm_counts"].append({
            "arm": arm, "city": city, "n": len(rs),
            "completed": sum(r.get("completed") == "true" for r in rs),
            "incomplete": sum(r.get("completed") != "true" for r in rs),
            "pool_n": frame_pools[arm, city],
            "draw_n": len(rs),
            "inclusion_probability": len(rs)/frame_pools[arm, city],
            "recent_capture": sum(capture_status(r, reference) == "recent" for r in rs),
            "recent_responses": {k: dict(Counter(
                r[k] for r in rs if r.get("completed") == "true"
                and capture_status(r, reference) == "recent"))
                for k in (["sidewalk"] if arm == "sidewalk" else
                          ["crossing_exists", "markings", "tactile", "kerb"])},
            "quality_counts": _quality_counts(rs, reference)})
    for arm in sorted({a for a, _ in cells}):
        cities = {c: rs for (a, c), rs in cells.items() if a == arm}
        pools = {c: frame_pools[arm, c] for c in cities}
        metrics = ["sidewalk_present"] if arm == "sidewalk" else [
            "crossing_present", "marked_crossing_present", "tactile_crossing_present",
            "lowered_or_flush_crossing_kerb_present"]
        for metric in metrics:
            ys = {c: [outcome(r, metric, reference) for r in rs] for c, rs in cities.items()}
            missing_reasons = {reason: 0 for reason in result["missing_reason_definitions"]}
            for city, members in cities.items():
                for row, value in zip(members, ys[city]):
                    if value is None:
                        missing_reasons[_missing_reason(row, metric, reference)] += 1
            entry = {"arm": arm, "metric": metric, "n": sum(map(len, ys.values())),
                     "cities": len(cities),
                     "denominator": "all sampled arm locations; attribute rates are joint with crossing presence",
                     "missing_reason_counts": missing_reasons,
                     "verifiable_count": sum(y is not None for values in ys.values() for y in values),
                     "equal_city": estimate(ys, {c: 1/len(cities) for c in cities}, pools),
                     "feature_weighted": estimate(
                         ys, {c: p/sum(pools.values()) for c, p in pools.items()}, pools),
                     "weight_note": "Pool-weighted local marker/way-frame estimate; not length-weighted "
                                    "or national. Selected cities are fixed.",
                     "pool_n": sum(pools.values()),
                     "by_city": {c: {"positive": v.count(1), "negative": v.count(0),
                                     "missing_reasons": dict(Counter(
                                         _missing_reason(r, metric, reference)
                                         for r, y in zip(cities[c], v) if y is None))}
                                 for c, v in sorted(ys.items())}}
            # Fully annotated cities form a fixed sub-frame: valid for those cities only.
            done = {c: v for c, v in ys.items()
                    if all(r.get("completed") == "true" for r in cities[c])}
            if done and len(done) < len(cities):
                done_pools = {c: pools[c] for c in done}
                entry["completed_cities"] = {
                    "cities": sorted(done), "n": sum(map(len, done.values())),
                    "verifiable_count": sum(y is not None for v in done.values() for y in v),
                    "scope": "Only the fully annotated cities, treated as a fixed sub-frame; "
                             "not an estimate for the remaining sampled cities or the full frame",
                    "equal_city": estimate(done, {c: 1/len(done) for c in done}, done_pools),
                    "feature_weighted": estimate(
                        done, {c: p/sum(done_pools.values()) for c, p in done_pools.items()},
                        done_pools),
                    "pool_n": sum(done_pools.values())}
            result["rates"].append(entry)
    p = {r["case_id"]: r for r in primary}
    pairs = [(p[r["case_id"]], r) for r in repeats if r.get("completed") == "true"]
    result["repeat_agreement"] = {
        k: {"pairs": sum(bool(a[k] and b[k]) for a,b in pairs),
            "agreements": sum(bool(a[k] and b[k]) and a[k] == b[k] for a,b in pairs),
            "verifiable_pairs": sum(bool(a[k] and b[k]) and
                                    a[k] not in ("unverifiable", "not_applicable") and
                                    b[k] not in ("unverifiable", "not_applicable") for a,b in pairs),
            "verifiable_agreements": sum(bool(a[k] and b[k]) and a[k] == b[k] and
                                         a[k] not in ("unverifiable", "not_applicable") for a,b in pairs)}
        for k in OUTCOMES}
    for entry in result["repeat_agreement"].values():
        entry["descriptive_agreement_rate"] = (
            entry["agreements"]/entry["pairs"] if entry["pairs"] else None)
        entry["descriptive_verifiable_agreement_rate"] = (
            entry["verifiable_agreements"]/entry["verifiable_pairs"]
            if entry["verifiable_pairs"] else None)
        entry["repeat_round_complete"] = full_repeat
    return result


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("--repeat-csv", help="Optional repeat-only v2 CSV; primary CSV may already contain both rounds")
    parser.add_argument("--sample", default=str(DEFAULT_SAMPLE), help="Canonical fixed sample CSV")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Immutable input used to draw the sample")
    parser.add_argument("--reference-date",
                        help="YYYY-MM-DD; default is latest completed primary audit_date, never today/mtime")
    parser.add_argument("--output", help="Optional analysis JSON file (.json); stdout JSON is always retained")
    args = parser.parse_args(argv)
    try:
        cases, manifest = load_expected_sample(args.sample, args.input)
        rows = _read_csv(args.csv)
        if args.repeat_csv:
            repeats = _read_csv(args.repeat_csv)
            if any(r.get("round") != "repeat" for r in repeats):
                raise ValueError("--repeat-csv must contain only repeat rows")
            rows += repeats
        result = analyse(rows, args.reference_date, expected_cases=cases, manifest=manifest)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            output = Path(args.output)
            inputs = [args.csv, args.repeat_csv, args.sample, args.input]
            if (output.suffix.lower() != ".json"
                    or output.resolve() in {Path(p).resolve() for p in inputs if p}):
                raise ValueError("--output must be a separate .json file, never an observation/input file")
            output.write_text(payload + "\n", encoding="utf-8")
    except (OSError, ValueError, csv.Error) as error:
        parser.error(str(error))
    print(payload)


if __name__ == "__main__":
    main()
