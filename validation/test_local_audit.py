"""Standard-library tests: python -B -m unittest discover -s validation -p test_*.py"""
import datetime as dt
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import local_audit as audit
import mapillary_fetch as fetch
import mapillary_join as join

spec = importlib.util.spec_from_file_location("build_harness", ROOT / "validation" / "build_harness.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class AuditTests(unittest.TestCase):
    def test_fingerprint_binds_content_and_input(self):
        a = [{"case_id": "C1", "lat": "38", "lon": "23", "arm": "sidewalk"}]
        fp = audit.fingerprint(a, "a"*64, "b"*64)
        self.assertEqual(fp, audit.fingerprint(a, "a"*64, "b"*64))
        self.assertNotEqual(fp, audit.fingerprint(a, "c"*64, "b"*64))
        self.assertNotEqual(fp, audit.fingerprint([{**a[0], "lat": "39"}], "a"*64, "b"*64))
        with patch.dict(audit.OUTCOMES, {"new_outcome": ["a"]}):
            self.assertNotEqual(fp, audit.fingerprint(a, "a"*64, "b"*64))

    def test_repeat_exactly_105_and_outcome_independent(self):
        cases = [{"case_id": "C"+str(i), "stratum": str(i//21)} for i in range(1050)]
        result = audit.repeat_ids(cases, "a"*64)
        self.assertEqual(len(set(result)), 105)
        self.assertEqual(result, audit.repeat_ids(list(reversed(cases)), "a"*64))

    def test_missing_bounds_and_city_weights(self):
        ys = {"A": [1, None], "B": [0, 0]}
        equal = audit.estimate(ys, {"A": .5, "B": .5})
        weighted = audit.estimate(ys, {"A": .9, "B": .1})
        self.assertEqual(equal["missing_bounds"], [.25, .5])
        self.assertEqual(weighted["missing_bounds"], [.45, .9])
        self.assertAlmostEqual(equal["verified_only_descriptive_rate"], 1/3)

    def test_zero_success_not_zero_uncertainty(self):
        result = audit.estimate({"A": [0]*7}, {"A": 1})
        self.assertGreater(result["conservative_95pct_interval"][1], .1)

    def test_stratified_variance_and_fpc(self):
        a = audit.estimate({"A": [0, 1]}, {"A": 1}, {"A": 2})
        self.assertEqual(a["endpoint_design_se"], [0., 0.])
        self.assertEqual(a["missing_bounds"], [.5, .5])

    def test_dates_old_future_and_imprecise(self):
        reference = dt.date(2026, 9, 15)
        for date in ["2020-01-01", "2027-01-01", "2021", "bad"]:
            self.assertFalse(audit.recent({"capture_date": date}, reference))
        for date in ["2022", "2026-09-01", "2021-09-15"]:
            self.assertTrue(audit.recent({"capture_date": date}, reference))

    def test_missing_not_deleted_from_attribute_denominator(self):
        r = {"completed": "true", "capture_date": "2026-01-01", "crossing_exists": "absent"}
        self.assertEqual(audit.outcome(r, "tactile_crossing_present", dt.date(2026, 9, 15)), 0)
        r["crossing_exists"] = "unverifiable"
        self.assertIsNone(audit.outcome(r, "tactile_crossing_present", dt.date(2026, 9, 15)))

    def test_legacy_answer_schema_rejected(self):
        with self.assertRaisesRegex(ValueError, "Legacy"):
            audit.validate_rows([{"case_id": "C1", "decision": "CONFIRMED"}])

    def test_full_schema_analysis_keeps_arms_and_rounds_separate(self):
        rows = []
        for city in ("A", "B"):
            for arm in ("sidewalk", "commission", "omission"):
                for i in range(7):
                    r = {k: "" for k in audit.FIELDS}
                    r.update(schema_version=audit.SCHEMA, sample_fingerprint="a"*64,
                             input_sha256="b"*64, round="primary", stratum=city, arm=arm,
                             case_id=city+arm+str(i), completed="true", source="field",
                             group=city, lat="38", lon="23", feature_id="way:"+str(len(rows)),
                             source_reference="survey", capture_date="2026-09-01",
                             audit_date="2026-09-01", first_audited_at="2026-09-01T00:00:00Z",
                             updated_at="2026-09-01T00:00:00Z",
                             pool_n="70", draw_n="7", inclusion_probability=".1",
                             radius_m="25", sampling_frame="exact-radius-v2",
                             pool_sha256="d"*64, seed="20260722")
                    if arm == "sidewalk":
                        r["sidewalk"] = "present_side_unspecified" if city == "A" else "none"
                    else:
                        r.update(crossing_exists="absent", markings="not_applicable",
                                 tactile="not_applicable", kerb="not_applicable")
                    rows.append(r)
        cases = [{k: r[k] for k in audit.IDENTITY} for r in rows]
        fp = audit.fingerprint(cases, "b"*64, "c"*64)
        for row in rows:
            row["sample_fingerprint"] = fp
        manifest = {"schema_version": audit.SCHEMA, "sample_fingerprint": fp,
                    "input_sha256": "b"*64, "sample_sha256": "c"*64,
                    "cases": len(cases), "cities": 2, "schema_fields": audit.FIELDS,
                    "identity_fields": audit.IDENTITY, "outcomes": audit.OUTCOMES,
                    "repeat_ids": audit.repeat_ids(cases, fp), "sampling_frames": ["exact-radius-v2"]}
        result = audit.analyse(rows, dt.date(2026, 9, 15), expected_cases=cases, manifest=manifest)
        self.assertEqual(len(result["rates"]), 9)
        sidewalk = next(x for x in result["rates"] if x["arm"] == "sidewalk")
        self.assertEqual(sidewalk["n"], 14)
        self.assertEqual(sidewalk["feature_weighted"]["missing_bounds"], [.5, .5])
        self.assertNotIn("completed_cities", sidewalk)
        self.assertEqual(sidewalk["by_city"]["A"], {"positive": 7, "negative": 0,
                                                    "missing_reasons": {}})
        partial = [{**r, "completed": ""} if r["stratum"] == "B" else r for r in rows]
        sub = next(x for x in audit.analyse(partial, dt.date(2026, 9, 15), expected_cases=cases,
                                            manifest=manifest)["rates"] if x["arm"] == "sidewalk")
        self.assertEqual(sub["equal_city"]["missing_bounds"], [.5, 1.])
        self.assertEqual(sub["completed_cities"]["cities"], ["A"])
        self.assertEqual(sub["completed_cities"]["equal_city"]["missing_bounds"], [1., 1.])
        self.assertEqual(sub["by_city"]["B"]["missing_reasons"], {"incomplete": 7})
        selected =next(r for r in rows if r["case_id"] in manifest["repeat_ids"])
        repeated = {**selected, "round": "repeat", "first_audited_at": "2026-09-14T00:00:00Z",
                    "updated_at": "2026-09-15T00:00:00Z", "audit_date": "2026-09-15"}
        with self.assertRaisesRegex(ValueError, "fourteen"):
            audit.analyse(rows+[repeated], dt.date(2026, 9, 15),
                          expected_cases=cases, manifest=manifest)
        repeated["first_audited_at"] = "2026-09-15T00:00:00Z"
        field = "sidewalk" if selected["arm"] == "sidewalk" else "crossing_exists"
        self.assertEqual(audit.analyse(rows+[repeated], dt.date(2026, 9, 15),
                                      expected_cases=cases, manifest=manifest)[
            "repeat_agreement"][field]["verifiable_pairs"], 1)

    def test_raw_profile_state_not_migrated(self):
        # The build explicitly reads mapped_feature_state only, never the legacy field.
        self.assertNotIn('r.get("profile_state"', Path(harness.__file__).read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "Node unavailable; Python tests still run")
    def test_browser_import_and_repeat_guards(self):
        c = {k: "" for k in audit.IDENTITY}
        c.update(case_id="C1", stratum="A", arm="sidewalk", lat="38", lon="23")
        meta = {"schema_version": audit.SCHEMA, "sample_fingerprint": "a"*64,
                "input_sha256": "b"*64, "identity_fields": audit.IDENTITY,
                "schema_fields": audit.FIELDS, "outcomes": audit.OUTCOMES, "repeat_ids": ["C1"]}
        html = harness.PAGE.replace("__DATA__", json.dumps([c])).replace("__META__", json.dumps(meta))
        js = re.search(r"<script>(.*?)</script>", html, re.S)[1]
        row = {k: "" for k in audit.FIELDS}
        row.update(c, schema_version=audit.SCHEMA, sample_fingerprint="a"*64,
                   input_sha256="b"*64, round="primary")
        code = "const vm=require('vm'), assert=require('assert');const ctx={};vm.runInNewContext("+json.dumps(js)+",ctx);"
        code += "const r="+json.dumps(row)+";assert.equal(ctx.auditTest.validateImport([r]).length,1);"
        code += "assert.throws(()=>ctx.auditTest.validateImport([{...r,lat:'39'}]));"
        code += "assert.throws(()=>ctx.auditTest.validateImport([{...r,sample_fingerprint:'c'.repeat(64)}]));"
        code += "assert.throws(()=>ctx.auditTest.validateImport([{case_id:'C1',decision:'CONFIRMED'}]));"
        code += "assert.throws(()=>ctx.auditTest.validateImport([r,r]));"
        code += "assert.equal(ctx.auditTest.repeatReady({},new Date('2026-09-15')),false);"
        code += "assert.equal(ctx.auditTest.repeatReady({C1:{completed:'true',updated_at:'2026-09-01T00:00:00Z'}},new Date('2026-09-15T00:00:00Z')),true);"
        code += "assert.equal(ctx.auditTest.repeatReady({C1:{completed:'true',updated_at:'2026-09-02T00:00:00Z'}},new Date('2026-09-15T00:00:00Z')),false);"
        subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_browser_save_preserves_legacy_and_failed_import_is_atomic(self):
        c = {k: "" for k in audit.IDENTITY}
        c.update(case_id="C1", stratum="A", arm="sidewalk", lat="38", lon="23")
        meta = {"schema_version": audit.SCHEMA, "sample_fingerprint": "a"*64,
                "input_sha256": "b"*64, "identity_fields": audit.IDENTITY,
                "schema_fields": audit.FIELDS, "outcomes": audit.OUTCOMES, "repeat_ids": ["C1"]}
        page = harness.PAGE.replace("__DATA__", json.dumps([c])).replace("__META__", json.dumps(meta))
        js = re.search(r"<script>(.*?)</script>", page, re.S)[1]
        setup = r"""
const vm=require('vm'),assert=require('assert');
const elements=new Map(),storage=new Map([['legacy-key','untouched']]),reads=[];
const el=id=>{if(!elements.has(id))elements.set(id,{value:id==='round'?'primary':'',textContent:'',style:{}});return elements.get(id);};
const today=new Date().toISOString().slice(0,10);
const fields=Object.entries({sidewalk:'both',source:'field',source_reference:'visit',
capture_date:today,audit_date:today,unverifiable_reason:'',notes:'local point'}).map(([k,v])=>({dataset:{field:k},value:v}));
const ctx={console,document:{getElementById:el,querySelectorAll:()=>fields,addEventListener:()=>{}},
localStorage:{getItem:k=>{reads.push(k);return storage.get(k)||null;},setItem:(k,v)=>storage.set(k,v)}};
"""
        checks = r"""
el('save').onclick();
const key='osm-local-audit-v2-'+'a'.repeat(64)+'-primary',saved=storage.get(key);
assert.equal(JSON.parse(saved).answers.C1.completed,'true');
assert.equal(storage.get('legacy-key'),'untouched');assert(!reads.includes('legacy-key'));
el('round').value='repeat';el('round').onchange();assert.equal(el('round').value,'primary');
assert.equal(storage.get(key),saved);
el('file').files=[{text:async()=> 'case_id,decision\nC1,CONFIRMED'}];
el('file').onchange().then(()=>{assert(el('error').textContent.startsWith('IMPORT REJECTED'));
assert.equal(storage.get(key),saved);});
"""
        p = subprocess.run(["node", "-e", setup+"vm.runInNewContext("+json.dumps(js)+",ctx);"+checks],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


class SpatialTests(unittest.TestCase):
    def test_two_longitude_cells_inside_25m(self):
        lat, lon = 38., 23.000249
        target = lon + .000253
        self.assertLess(join.haversine(lat, lon, lat, target), 25)
        self.assertTrue(join.Index([(lat, target)]).near(lat, lon))

    def test_distance_not_occupied_square(self):
        self.assertFalse(join.Index([(38.0004, 23.)]).near(38., 23.))

    def test_multiple_matches_are_not_unique_missing_crossings(self):
        idx = join.Index([(38., 23., "same")])
        self.assertEqual(idx.matches(38., 23.), idx.matches(38.00001, 23.))

    def test_polygon_hole_and_outside(self):
        geom = {"type": "Polygon", "coordinates": [
            [[0,0],[3,0],[3,3],[0,3],[0,0]], [[1,1],[2,1],[2,2],[1,2],[1,1]]]}
        self.assertTrue(fetch.inside_geometry(.5, .5, geom))
        self.assertFalse(fetch.inside_geometry(1.5, 1.5, geom))
        self.assertFalse(fetch.inside_geometry(4, 1, geom))

    def test_legacy_empty_is_unknown(self):
        f = {"geometry": {"type": "Polygon", "coordinates": [[[23,38],[23.003,38],
                    [23.003,38.003],[23,38.003],[23,38]]]}}
        with patch.object(join, "cached", return_value={"status": "legacy", "data": []}), \
             patch.object(join.os.path, "isfile", return_value=False):
            coverage = join.TileCoverage([f])
        self.assertEqual(coverage.at(38.001, 23.001), "unknown")
        self.assertEqual(coverage.at(38.009, 23.009), "unknown")

    def test_clipped_tile_does_not_expand_coverage(self):
        f = {"geometry": {"type": "Polygon", "coordinates": [[[23,38],[23.003,38],
                    [23.003,38.003],[23,38.003],[23,38]]]}}
        with patch.object(join, "cached", return_value={"status": "ok", "data": [{"id": "x"}]}), \
             patch.object(join.os.path, "isfile", return_value=False):
            coverage = join.TileCoverage([f])
        self.assertEqual(coverage.at(38.001, 23.001), "image_positive")
        self.assertEqual(coverage.at(38.009, 23.009), "unknown")

    def test_acquisition_errors_not_empty_success(self):
        with patch.object(fetch.os.path, "isfile", return_value=True), \
             patch.object(fetch, "load", return_value={"status": "error", "data": []}):
            self.assertEqual(fetch.cached("unused")["status"], "error")

    def test_pagination_consumed(self):
        with patch.object(fetch, "fetch", side_effect=[
                {"data": [1], "paging": {"next": "https://graph.mapillary.com/next"}},
                {"data": [2], "paging": None}]):
            self.assertEqual(fetch.fetch_pages("https://graph.mapillary.com/first"), [1,2])

    def test_error_payload_not_empty_success(self):
        with self.assertRaisesRegex(RuntimeError, "data array"):
            fetch.page_data({"error": {"message": "unavailable"}})

    def test_untrusted_pagination_host_rejected(self):
        with self.assertRaises(RuntimeError):
            fetch.fetch_pages("https://example.org/")


if __name__ == "__main__":
    unittest.main()
