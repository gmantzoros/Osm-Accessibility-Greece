"""Canonical-frame local audit regressions; no generated UI or real exports."""
import contextlib
import copy
import csv
import datetime as dt
import io
import json
import re
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import local_audit as audit

REFERENCE = dt.date(2026, 9, 15)


def manifest_for(cases):
    fp = audit.fingerprint(cases, "b"*64, "c"*64)
    return {"schema_version": audit.SCHEMA, "sample_fingerprint": fp,
            "input_sha256": "b"*64, "sample_sha256": "c"*64,
            "cases": len(cases), "cities": len({c["stratum"] for c in cases}),
            "schema_fields": list(audit.FIELDS), "identity_fields": list(audit.IDENTITY),
            "outcomes": copy.deepcopy(audit.OUTCOMES),
            "repeat_ids": audit.repeat_ids(cases, fp),
            "sampling_frames": sorted({c["sampling_frame"] for c in cases})}


def synthetic_cases():
    cases = []
    for city, pool in (("A", 40), ("B", 200)):
        for i in range(10):
            case = {k: "" for k in audit.IDENTITY}
            case.update(case_id=city+str(i), group="proxy_"+city, stratum=city,
                        arm="sidewalk", lat="38.0001", lon="23.0001",
                        feature_id="way:"+str(len(cases)+1),
                        osm_assertion="sidewalk assertion (way "+str(len(cases)+1)+")",
                        pool_n=str(pool), draw_n="10", inclusion_probability=str(10/pool),
                        radius_m="25", sampling_frame="exact-radius-v2",
                        pool_sha256="d"*64, seed="20260722")
            cases.append(case)
    return cases


def primary_row(case, manifest):
    row = {k: "" for k in audit.FIELDS}
    row.update({k: case[k] for k in audit.IDENTITY})
    row.update(schema_version=audit.SCHEMA,
               sample_fingerprint=manifest["sample_fingerprint"],
               input_sha256=manifest["input_sha256"], round="primary",
               completed="true", source="field", source_reference="observer visit",
               capture_date="2022", audit_date="2026-09-01",
               first_audited_at="2026-09-01T00:00:00Z",
               updated_at="2026-09-01T00:00:00Z",
               sidewalk="both" if case["stratum"] == "A" else "none")
    return row


class CanonicalValidationTests(unittest.TestCase):
    def setUp(self):
        self.cases = synthetic_cases()
        self.manifest = manifest_for(self.cases)
        self.rows = [primary_row(c, self.manifest) for c in self.cases]

    def analyse(self, rows=None, reference=REFERENCE, cases=None, manifest=None):
        return audit.analyse(self.rows if rows is None else rows, reference,
                             expected_cases=self.cases if cases is None else cases,
                             manifest=self.manifest if manifest is None else manifest)

    def repeats(self):
        primary = {r["case_id"]: r for r in self.rows}
        return [{**primary[case_id], "round": "repeat", "audit_date": "2026-09-15",
                 "first_audited_at": "2026-09-15T00:00:00Z",
                 "updated_at": "2026-09-15T00:00:00Z"}
                for case_id in self.manifest["repeat_ids"]]

    def test_analysis_requires_external_canonical_cases_and_manifest(self):
        with self.assertRaises(TypeError):
            audit.analyse(self.rows, REFERENCE)
        with self.assertRaisesRegex(ValueError, "Canonical"):
            audit.validate_rows(self.rows)
        with self.assertRaisesRegex(ValueError, "Canonical"):
            self.analyse(cases=[])

    def test_truncated_case_and_entire_missing_city_are_rejected(self):
        for rows in (self.rows[:-1], [r for r in self.rows if r["stratum"] == "A"]):
            with self.subTest(n=len(rows)), self.assertRaisesRegex(ValueError, "Missing canonical primary"):
                self.analyse(rows)
        with self.assertRaisesRegex(ValueError, "Missing canonical primary"):
            self.analyse(self.repeats())

    def test_unknown_and_duplicate_cases_are_rejected(self):
        unknown = {**self.rows[0], "case_id": "UNKNOWN"}
        for rows, message in ((self.rows[1:]+[unknown], "Unknown canonical"),
                              (self.rows+[self.rows[0]], "Duplicate"),
                              (self.rows+self.repeats()+[self.repeats()[0]], "Duplicate")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.analyse(rows)

    def test_every_immutable_identity_field_is_bound(self):
        for field in audit.IDENTITY:
            changed = {**self.rows[0], field: self.rows[0][field]+"-tampered"}
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "Unknown canonical|Immutable case identity"):
                self.analyse([changed]+self.rows[1:])

    def test_hashes_must_equal_canonical_not_merely_be_well_formed(self):
        for field in ("sample_fingerprint", "input_sha256"):
            for value in ("e"*64, "wrong", "E"*64):
                rows = [{**r, field: value} for r in self.rows]
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                        ValueError, "SHA-256|canonical"):
                    self.analyse(rows)

    def test_each_row_requires_schema_and_known_completion_flag(self):
        missing = dict(self.rows[-1])
        del missing["seed"]
        with self.assertRaisesRegex(ValueError, "Legacy/incomplete"):
            self.analyse(self.rows[:-1]+[missing])
        for flag in ("TRUE", "yes", "0", None):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "completion flag"):
                self.analyse([{**self.rows[0], "completed": flag}]+self.rows[1:])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.analyse([{**self.rows[0], "schema_version": "legacy"}]+self.rows[1:])

    def test_canonical_probability_metadata_cannot_disable_pool_weights(self):
        changes = [("pool_n", v) for v in ("0", "-1", "9", "40.0", "NaN")]
        changes += [("draw_n", v) for v in ("0", "9", "11", "10.0")]
        changes += [("inclusion_probability", v) for v in
                    ("nan", "inf", "-inf", "0", "-0.1", "1.1", ".1", "bad")]
        changes += [("pool_sha256", "E"*64)]
        for field, value in changes:
            cases = [{**c, field: value} if c["stratum"] == "A" else dict(c)
                     for c in self.cases]
            manifest = manifest_for(cases)
            rows = [primary_row(c, manifest) for c in cases]
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    ValueError, "probability|pool|draw|allocation"):
                self.analyse(rows, cases=cases, manifest=manifest)

    def test_mixed_cell_metadata_is_rejected_even_with_recomputed_fingerprint(self):
        for field, value in (("pool_n", "41"), ("draw_n", "9"), ("seed", "different"),
                             ("group", "other"), ("pool_sha256", "e"*64),
                             ("sampling_frame", "other-frame")):
            cases = copy.deepcopy(self.cases)
            cases[0][field] = value
            manifest = manifest_for(cases)
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "Inconsistent|allocation"):
                self.analyse([primary_row(c, manifest) for c in cases],
                             cases=cases, manifest=manifest)

    def test_canonical_manifest_is_self_consistent(self):
        changes = (("cases", 19), ("cities", 1), ("cases", 20.0), ("cities", 2.0),
                   ("sample_sha256", "not-a-hash"),
                   ("sample_fingerprint", "e"*64), ("identity_fields", []),
                   ("sampling_frames", ["wrong"]), ("repeat_ids", ["A0"]),
                   ("repeat_ids", list(reversed(self.manifest["repeat_ids"]))))
        for field, value in changes:
            manifest = {**self.manifest, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "canonical|Canonical"):
                self.analyse(manifest=manifest)
        cases = copy.deepcopy(self.cases)
        cases[-1]["case_id"] = cases[0]["case_id"]
        with self.assertRaisesRegex(ValueError, "duplicate canonical"):
            self.analyse(cases=cases, manifest=manifest_for(cases))

    def test_blank_and_false_incomplete_rows_keep_full_denominator(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["completed"] = ""
        rows[1]["completed"] = "false"
        result = self.analyse(rows)
        self.assertEqual(result["expected_primary_count"], 20)
        self.assertEqual(result["completed_primary_count"], 18)
        self.assertEqual(result["incomplete_primary_count"], 2)
        self.assertFalse(result["full_primary"])
        rate = result["rates"][0]
        self.assertEqual(rate["n"], 20)
        self.assertEqual(rate["equal_city"]["missing_bounds"], [.4, .5])
        self.assertAlmostEqual(rate["feature_weighted"]["missing_bounds"][0], 8/60)
        self.assertAlmostEqual(rate["feature_weighted"]["missing_bounds"][1], 1/6)
        self.assertEqual(rate["missing_reason_counts"]["incomplete"], 2)

    def test_temporal_exclusions_have_counts_and_missing_reasons(self):
        rows = copy.deepcopy(self.rows)
        values = ("", "bad", "2020", "2027", "2021", "2022", "2026")
        for row, value in zip(rows, values):
            row["capture_date"] = value
        rows[7]["completed"] = "false"
        result = self.analyse(rows)
        quality = result["quality_counts"]["primary"]
        reasons = result["rates"][0]["missing_reason_counts"]
        for reason in ("incomplete", "undated", "invalid", "stale", "future", "ambiguous_cutoff"):
            self.assertEqual(quality[reason], 1, reason)
            self.assertEqual(reasons[reason], 1, reason)
        self.assertEqual(quality["year_only_current_year_ambiguity"], 1)
        self.assertEqual(result["rates"][0]["equal_city"]["missing_bounds"], [.2, .5])
        self.assertIn("Year-only", " ".join(result["source_quality_limitations"]))
        self.assertEqual(sum(reasons.values()), 6)

    def test_recent_rule_boundary_and_leap_reference_are_unchanged(self):
        for value, status in (("2020", "stale"), ("2021", "ambiguous_cutoff"),
                              ("2027", "future"), ("bad", "invalid"),
                              ("2022", "recent"), ("2021-09-15", "recent")):
            with self.subTest(value=value):
                self.assertEqual(audit.capture_status({"capture_date": value}, REFERENCE), status)
                self.assertEqual(audit.recent({"capture_date": value}, REFERENCE), status == "recent")
        self.assertTrue(audit.recent({"capture_date": "2019-02-28"}, dt.date(2024, 2, 29)))
        self.assertFalse(audit.recent({"capture_date": "2019-02-27"}, dt.date(2024, 2, 29)))

    def test_missing_or_invalid_capture_value_does_not_crash_or_verify(self):
        for value, status in ((None, "undated"), (42, "invalid"), ("2026-02-30", "invalid")):
            rows = [{**self.rows[0], "capture_date": value}]+self.rows[1:]
            with self.subTest(value=value):
                result = self.analyse(rows)
                self.assertEqual(result["rates"][0]["missing_reason_counts"][status], 1)
                self.assertEqual(result["rates"][0]["verifiable_count"], 19)

    def test_source_v2_year_only_and_optional_reference_remain_supported(self):
        rows = [{**r, "source": "streetview", "source_reference": ""} for r in self.rows]
        result = self.analyse(rows)
        self.assertEqual(result["rates"][0]["verifiable_count"], 20)
        self.assertEqual(result["quality_counts"]["primary"]["missing_source_reference"], 20)
        self.assertIn("do not prove", " ".join(result["source_quality_limitations"]))
        self.assertNotIn("panorama_id", audit.FIELDS)

    def test_unverifiable_is_unknown_not_an_observed_absence(self):
        row = {**self.rows[0], "sidewalk": "unverifiable", "unverifiable_reason": "occluded"}
        result = self.analyse([row]+self.rows[1:])
        self.assertEqual(result["rates"][0]["missing_reason_counts"]["unverifiable"], 1)
        self.assertEqual(result["rates"][0]["equal_city"]["missing_bounds"], [.45, .5])
        row["unverifiable_reason"] = ""
        with self.assertRaisesRegex(ValueError, "require a reason"):
            self.analyse([row]+self.rows[1:])

    def test_reference_defaults_to_latest_completed_primary_date_only(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["audit_date"] = "2026-09-03"
        rows[1]["completed"] = "false"
        rows[1]["audit_date"] = "2099-01-01"
        result = self.analyse(rows, reference=None)
        self.assertEqual(result["reference_date"], "2026-09-03")
        self.assertEqual(result["reference_date_source"], "latest_completed_primary_audit_date")
        rows[1]["completed"] = "true"
        rows[1]["audit_date"] = "2026-09-01"
        repeats = self.repeats()
        result = self.analyse(rows+repeats, reference=None)
        self.assertEqual(result["reference_date"], "2026-09-03")
        self.assertEqual(self.analyse(reference="2026-09-15")["reference_date_source"], "explicit")

    def test_pending_audit_needs_explicit_reference_not_today(self):
        rows = [{**r, "completed": ""} for r in self.rows]
        with self.assertRaisesRegex(ValueError, "explicit reference date"):
            self.analyse(rows, reference=None)
        result = self.analyse(rows)
        self.assertEqual(result["rates"][0]["equal_city"]["missing_bounds"], [0., 1.])
        for reference in ("bad", dt.datetime(2026, 9, 15), 2026):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "reference date|Reference date"):
                self.analyse(reference=reference)
        with self.assertRaisesRegex(ValueError, "audit_date"):
            self.analyse([{**self.rows[0], "audit_date": "bad"}]+self.rows[1:])

    def test_repeat_selection_is_enforced_for_completed_and_unfinished_rows(self):
        row = next(r for r in self.rows if r["case_id"] not in self.manifest["repeat_ids"])
        for completed in ("true", "false", ""):
            repeat = {**row, "round": "repeat", "completed": completed}
            with self.subTest(completed=completed), self.assertRaisesRegex(ValueError, "selected repeat"):
                self.analyse(self.rows+[repeat])

    def test_completed_repeat_requires_all_primary_cases_completed(self):
        for completed in ("false", ""):
            rows = [{**self.rows[0], "completed": completed}]+self.rows[1:]
            with self.subTest(completed=completed), self.assertRaisesRegex(ValueError, "complete primary"):
                self.analyse(rows+self.repeats())

    def test_gap_uses_maximum_primary_update_and_accepts_exact_fourteen_days(self):
        rows = copy.deepcopy(self.rows)
        rows[-1]["updated_at"] = "2026-09-02T00:00:00Z"
        repeats = self.repeats()
        with self.assertRaisesRegex(ValueError, "fourteen"):
            self.analyse(rows+repeats)
        for row in repeats:
            row.update(first_audited_at="2026-09-16T00:00:00Z",
                       updated_at="2026-09-16T00:00:00Z", audit_date="2026-09-16")
        self.assertTrue(self.analyse(rows+repeats)["full_repeat"])

    def test_repeat_timing_requires_timezone_aware_parseable_timestamps(self):
        for field, value, target in (("updated_at", "", "primary"),
                                     ("updated_at", "2026-09-01T00:00:00", "primary"),
                                     ("updated_at", "bad", "primary"),
                                     ("first_audited_at", "2026-09-15", "repeat"),
                                     ("first_audited_at", "2026-09-15T00:00:00", "repeat"),
                                     ("first_audited_at", "bad", "repeat"),
                                     ("updated_at", "2026-09-15T00:00:00", "repeat")):
            rows, repeats = copy.deepcopy(self.rows), self.repeats()
            (rows if target == "primary" else repeats)[0][field] = value
            with self.subTest(field=field, value=value, target=target), self.assertRaisesRegex(
                    ValueError, "timezone"):
                self.analyse(rows+repeats)

    def test_timezone_offsets_are_normalized_not_treated_as_naive_local_times(self):
        repeats = self.repeats()
        repeats[0].update(first_audited_at="2026-09-15T01:00:00+01:00",
                          updated_at="2026-09-15T02:00:00+02:00")
        self.assertTrue(self.analyse(self.rows+repeats)["full_repeat"])
        repeats[0]["first_audited_at"] = "2026-09-15T00:00:00+01:00"
        with self.assertRaisesRegex(ValueError, "fourteen"):
            self.analyse(self.rows+repeats)
        repeats[0].update(first_audited_at="2026-09-15T00:00:00Z",
                          updated_at="2026-09-14T23:59:59Z")
        with self.assertRaisesRegex(ValueError, "precedes first"):
            self.analyse(self.rows+repeats)

    def test_partial_repeat_is_incomplete_descriptive_agreement_not_reliability(self):
        result = self.analyse(self.rows+self.repeats()[:1])
        self.assertTrue(result["full_primary"])
        self.assertFalse(result["full_repeat"])
        self.assertEqual(result["repeat_status"], "incomplete")
        self.assertEqual(result["expected_repeat_count"], 2)
        self.assertEqual(result["supplied_repeat_count"], 1)
        self.assertEqual(result["completed_repeat_count"], 1)
        self.assertEqual(result["missing_repeat_count"], 1)
        agreement = result["repeat_agreement"]["sidewalk"]
        self.assertEqual(agreement["verifiable_pairs"], 1)
        self.assertFalse(agreement["repeat_round_complete"])
        self.assertIn("not full reliability", " ".join(result["source_quality_limitations"]))

    def test_repeat_absence_pending_rows_and_complete_round_have_distinct_status(self):
        result = self.analyse()
        self.assertEqual(result["repeat_status"], "not_supplied")
        self.assertFalse(result["full_repeat"])
        self.assertIsNone(result["repeat_agreement"]["sidewalk"]["descriptive_agreement_rate"])
        rows = [{**self.rows[0], "completed": ""}]+self.rows[1:]
        repeat = {**self.repeats()[0], "completed": "", "first_audited_at": "", "updated_at": ""}
        result = self.analyse(rows+[repeat])
        self.assertEqual(result["incomplete_repeat_count"], 1)
        self.assertEqual(result["completed_repeat_count"], 0)
        result = self.analyse(self.rows+self.repeats())
        self.assertEqual(result["repeat_status"], "complete")
        self.assertTrue(result["full_repeat"])
        self.assertEqual(result["repeat_agreement"]["sidewalk"]["verifiable_pairs"], 2)


class LoaderAndCLITests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "validation" / (".local-audit-test-"+uuid.uuid4().hex)
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory)
        self.sample = self.directory / "sample.csv"
        self.input = self.directory / "immutable-input.pbf"
        self.input.write_bytes(b"synthetic immutable input; not an OSM download")
        self.seed = self.directory / "audit_sample_seed.txt"
        self.seed.write_text("seed=20260722\ncases_written=20\nscope_strata=2\n"
                             "per_group_per_arm=10\nsidewalk_pool=240\n"
                             "sampling_frame=exact-radius-v2\npool_sha256="+"d"*64+"\n",
                             encoding="utf-8")
        self.write_sample(synthetic_cases())

    def write_sample(self, cases):
        with self.sample.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=audit.IDENTITY)
            writer.writeheader()
            writer.writerows(cases)

    def write_rows(self, path, rows):
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=audit.FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def cli_args(self, primary):
        return [str(primary), "--sample", str(self.sample), "--input", str(self.input)]

    def test_loader_reuses_builder_feature_radius_seed_defaults(self):
        cases = synthetic_cases()
        for case in cases:
            case.update(feature_id="", radius_m="", seed="")
        cases[1]["osm_assertion"] = ""
        self.write_sample(cases)
        expected, manifest = audit.load_expected_sample(self.sample, self.input)
        self.assertEqual(expected[0]["feature_id"], "way:1")
        self.assertEqual(expected[1]["feature_id"], "sidewalk-coordinate:38.0001,23.0001")
        self.assertEqual({c["radius_m"] for c in expected}, {"25"})
        self.assertEqual({c["seed"] for c in expected}, {"20260722"})
        self.assertEqual(manifest["input_sha256"], audit.digest_file(self.input))
        self.assertEqual(manifest["sample_sha256"], audit.digest_file(self.sample))
        self.assertEqual(manifest["sample_fingerprint"], audit.fingerprint(
            expected, manifest["input_sha256"], manifest["sample_sha256"]))

    def test_loader_rejects_missing_frame_and_bad_seed_allocation(self):
        with self.assertRaisesRegex(ValueError, "sample file is unavailable"):
            audit.load_expected_sample(self.directory / "absent.csv", self.input)
        with self.assertRaisesRegex(ValueError, "input file is unavailable"):
            audit.load_expected_sample(self.sample, self.directory / "absent.pbf")
        for value in ("cases_written=19", "scope_strata=1", "per_group_per_arm=9",
                      "sidewalk_pool=241", "seed=other", "input_sha256="+"e"*64):
            self.seed.write_text(value+"\n", encoding="utf-8")
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "seed metadata"):
                audit.load_expected_sample(self.sample, self.input)

    def test_loader_rejects_malformed_canonical_schema_as_validation_error(self):
        self.sample.write_text("stratum,arm\nA,sidewalk\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "canonical sample schema"):
            audit.load_expected_sample(self.sample, self.input)

    def test_loader_rejects_truncated_canonical_draw_not_only_truncated_answers(self):
        cases = synthetic_cases()
        self.write_sample(cases[:-1])
        with self.assertRaisesRegex(ValueError, "allocation"):
            audit.load_expected_sample(self.sample, self.input)
        self.write_sample([c for c in cases if c["stratum"] == "A"])
        with self.assertRaisesRegex(ValueError, "seed metadata allocation"):
            audit.load_expected_sample(self.sample, self.input)

    def test_loader_rejects_sample_bound_to_different_immutable_input(self):
        with self.sample.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        with self.sample.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=audit.IDENTITY+["input_sha256"])
            writer.writeheader()
            writer.writerows({**r, "input_sha256": "e"*64} for r in rows)
        with self.assertRaisesRegex(ValueError, "input hash differs"):
            audit.load_expected_sample(self.sample, self.input)

    def test_cli_stdout_only_and_reproducible_default_reference(self):
        cases, manifest = audit.load_expected_sample(self.sample, self.input)
        rows = [primary_row(c, manifest) for c in cases]
        rows[0]["audit_date"] = "2026-09-03"
        path = self.directory / "primary.csv"
        self.write_rows(path, rows)
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            audit.main(self.cli_args(path))
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["reference_date"], "2026-09-03")
        self.assertEqual(result["expected_primary_count"], 20)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_cli_separate_repeat_json_and_explicit_reference(self):
        cases, manifest = audit.load_expected_sample(self.sample, self.input)
        rows = [primary_row(c, manifest) for c in cases]
        primary = {r["case_id"]: r for r in rows}
        repeats = [{**primary[case_id], "round": "repeat", "audit_date": "2026-09-15",
                    "first_audited_at": "2026-09-15T00:00:00Z", "updated_at": "2026-09-15T00:00:00Z"}
                   for case_id in manifest["repeat_ids"]]
        path, repeat_path = self.directory / "primary.csv", self.directory / "repeat.csv"
        output = self.directory / "analysis.json"
        self.write_rows(path, rows)
        self.write_rows(repeat_path, repeats)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            audit.main(self.cli_args(path)+["--repeat-csv", str(repeat_path),
                                           "--reference-date", "2026-09-15", "--output", str(output)])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))
        self.assertTrue(result["full_repeat"])
        self.assertEqual(result["reference_date_source"], "explicit")

    def test_cli_rejects_missing_cases_and_cannot_overwrite_observations(self):
        cases, manifest = audit.load_expected_sample(self.sample, self.input)
        rows = [primary_row(c, manifest) for c in cases]
        path = self.directory / "primary.csv"
        self.write_rows(path, rows[:-1])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            audit.main(self.cli_args(path))
        self.assertEqual(error.exception.code, 2)
        self.assertIn("Missing canonical primary", stderr.getvalue())
        self.write_rows(path, rows)
        original = path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            audit.main(self.cli_args(path)+["--output", str(path)])
        self.assertEqual(path.read_bytes(), original)


@unittest.skipUnless(all((ROOT / p).is_file() for p in ("results/census/audit_sample.csv", "data/greece-latest.osm.pbf")),
                     "needs the unpublished local audit draw and input extract")
class FrozenSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.manifest = audit.load_expected_sample()
        html = (ROOT / "validation" / "audit_v2.html").read_text(encoding="utf-8")
        match = re.search(r"const DATA=(.*?), META=(.*?);\n", html, re.S)
        cls.html_cases, cls.html_manifest = json.loads(match[1]), json.loads(match[2])

    def test_real_500_case_fingerprint_matches_existing_html_byte_for_byte_identity(self):
        self.assertEqual(self.cases, self.html_cases)
        self.assertEqual(self.manifest, self.html_manifest)
        self.assertEqual(self.manifest["sample_fingerprint"],
                         "cc73929613ac17f9b1f16c11d7d6e0e1e27c98a0410280c5c4419dd4bd320962")
        self.assertEqual(self.manifest["sample_sha256"],
                         "df69117f043ab2389316c3847f387411f7e31a053c005f4ffee38f119162227d")
        self.assertEqual(self.manifest["input_sha256"],
                         "7f973ac69ac39e8a91999e49342de1d9986b70017ca41bc1f5107f573fa101b0")
        self.assertEqual(self.manifest["schema_fields"], audit.FIELDS)
        self.assertEqual(self.manifest["identity_fields"], audit.IDENTITY)
        self.assertEqual(self.manifest["outcomes"], audit.OUTCOMES)
        self.assertEqual(len(self.cases), 500)
        self.assertEqual(self.manifest["cities"], 50)
        self.assertEqual(len(self.manifest["repeat_ids"]), 50)

    def test_real_frame_pending_rows_are_in_memory_unknown_not_fabricated_results(self):
        rows = [{**primary_row(c, self.manifest), "completed": "", "source": "",
                 "source_reference": "", "capture_date": "", "audit_date": "",
                 "sidewalk": "", "first_audited_at": "", "updated_at": ""}
                for c in self.cases]
        result = audit.analyse(rows, REFERENCE, expected_cases=self.cases, manifest=self.manifest)
        self.assertEqual(result["expected_primary_count"], 500)
        self.assertEqual(result["completed_primary_count"], 0)
        self.assertEqual(result["incomplete_primary_count"], 500)
        self.assertEqual(result["expected_repeat_count"], 50)
        self.assertFalse(result["full_primary"])
        self.assertFalse(result["full_repeat"])
        rate = result["rates"][0]
        self.assertEqual(rate["cities"], 50)
        self.assertEqual(rate["pool_n"], 102136)
        for estimand in ("equal_city", "feature_weighted"):
            self.assertAlmostEqual(rate[estimand]["missing_bounds"][0], 0.)
            self.assertAlmostEqual(rate[estimand]["missing_bounds"][1], 1.)


if __name__ == "__main__":
    unittest.main()
