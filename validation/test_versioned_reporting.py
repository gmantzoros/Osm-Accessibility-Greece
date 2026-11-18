"""In-memory fixture checks for the isolated corrected reporting route."""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import aggregate
import paper_data
import targeting_distance
from corrected_model_impact import CORRECTED_VERSION
from cross_country import COUNTRIES, GREECE


FIXTURE_ROOT = ROOT / "validation" / "fixtures" / "reporting-fixture"


class CorrectedPathTests(unittest.TestCase):
    def setUp(self):
        self.paths = aggregate.reporting_paths(FIXTURE_ROOT, versioned=True)

    def test_isolated_paths_and_no_implicit_legacy_mode(self):
        p = self.paths
        self.assertEqual(p["census"], FIXTURE_ROOT / "results" / "impact" /
                         "greece-full-strata-conditional-2026-10-09")
        self.assertEqual(p["bundle"], p["base"] / "paper_data.json")
        self.assertEqual(p["figures"], p["base"] / "paper" / "figures")
        self.assertNotEqual(p["analysis"], FIXTURE_ROOT / "results" / "analysis")
        self.assertNotEqual(p["paper"], FIXTURE_ROOT / "paper")
        with self.assertRaisesRegex(ValueError, "only the verified"):
            aggregate.main("results/census", versioned=True, root=FIXTURE_ROOT)
        with self.assertRaisesRegex(FileNotFoundError, "corrected Greece run manifest"):
            aggregate.verify_corrected_greek(p)

    def test_greek_manifest_requires_every_downstream_file_and_one_model(self):
        outputs = {name: "hash" for name in (
            "summary.csv", "crossing_metrics.csv", "way_metrics.csv", "destinations.csv",
            "provenance.csv", "tag_values.csv", "feasibility.csv", "feasibility_global.csv",
            "feasibility_sensitivity.csv", "edit_targets.csv", "edit_targets_summary.csv",
            "edit_targets_efficiency.csv", "edit_targets_length_baseline.csv", "spatial_coverage.csv")}
        manifest = {"output_sha256": outputs}
        correct = (
            "stratum,model_version,profile,graph_scope,component_threshold_km\n"
            "NATIONAL,conditional-2026-10-09,bvi-v0,extended,1.000\n")

        def verify(text):
            with (patch("pathlib.Path.is_file", return_value=True),
                  patch("pathlib.Path.read_text", return_value=json.dumps(manifest)),
                  patch("pathlib.Path.open", mock_open(read_data=text)),
                  patch("aggregate.check_manifest")):
                return aggregate.verify_corrected_greek(self.paths)

        self.assertEqual(verify(correct)["output_sha256"], outputs)
        outputs.pop("edit_targets_efficiency.csv")
        with self.assertRaisesRegex(ValueError, "lacks downstream files"):
            verify(correct)
        outputs["edit_targets_efficiency.csv"] = "hash"
        with self.assertRaisesRegex(ValueError, "mixed model versions"):
            verify(correct.replace("conditional-2026-10-09", "conditional-2026-09-15"))

    def test_manifests_bind_versioned_outputs_to_sources(self):
        p = self.paths
        inputs = [p["census"] / "run_manifest.json", p["root"] / "analysis" / "aggregate.py"]
        outputs = [p["analysis"] / "by_region.csv"]
        with (patch("aggregate.digest", side_effect=lambda path: Path(path).name),
              patch("pathlib.Path.write_text", autospec=True) as write):
            saved = aggregate.write_versioned_manifest(p, "aggregate", inputs, outputs, p["analysis"])
        self.assertEqual(Path(write.call_args.args[0]).parent, p["analysis"])
        self.assertIn("results/impact/unified-conditional-2026-10-09/analysis/by_region.csv",
                      saved["output_sha256"])
        with (patch("aggregate.digest", side_effect=lambda path: Path(path).name),
              patch("pathlib.Path.is_file", return_value=True),
              patch("pathlib.Path.read_text", return_value=json.dumps(saved))):
            self.assertEqual(aggregate.verify_versioned_manifest(
                p, "aggregate", inputs, ["by_region.csv"], p["analysis"]), saved)
        with (patch("aggregate.digest", side_effect=lambda path: "modified"
                    if Path(path).name == "by_region.csv" else Path(path).name),
              patch("pathlib.Path.is_file", return_value=True),
              patch("pathlib.Path.read_text", return_value=json.dumps(saved))):
            with self.assertRaisesRegex(ValueError, "Stale or mixed"):
                aggregate.verify_versioned_manifest(
                    p, "aggregate", inputs, ["by_region.csv"], p["analysis"])
        with self.assertRaisesRegex(ValueError, "outside the versioned bundle"):
            aggregate.write_versioned_manifest(
                p, "aggregate", inputs, outputs, p["root"] / "results" / "analysis")


class UnifiedInputFixtureTests(unittest.TestCase):
    def setUp(self):
        self.paths = aggregate.reporting_paths(FIXTURE_ROOT, versioned=True)
        self.greek = {
            "input_sha256": {"pbf": "pbf", "jar": "corrected-jar", "profile": "bvi-profile"},
            "source_sha256": {"gr/Main.java": "source"},
            "command": ["java", "-Xmx5g", "-jar", str(FIXTURE_ROOT / "stage" / "census.jar")],
        }
        self.national = {
            "stratum": "NATIONAL", "layer": "national", "group": "",
            "profile": "bvi-v0", "graph_scope": "extended", "model_version": CORRECTED_VERSION,
            "component_threshold_km": "1", "walkable_km": "100",
            "eligible_km": "100", "pess_eligible_usable_km": "20",
            "opt_eligible_usable_km": "80",
        }
        self.summary = {
            "stratum": "NATIONAL", "drivable_km": "100", "intersections": "50",
            "crossings": "10", "tactile_pct": "5", "name_pct": "20",
            "sidewalk_pct": "20", "bpri_v1": "0.5",
        }
        self.crossings = {
            "stratum": "NATIONAL", "signal_sound": "3", "signal_sound_yes": "2",
            "crossing_sound": "1", "crossing_conflicts": "0",
        }
        self.expected = [GREECE, *COUNTRIES]
        self.countries = []
        self.provenance = {"excluded_or_failed": [],
                           "requested_slugs": [entry[0] for entry in self.expected],
                           "inputs": []}
        self.profiles = []
        self.country_summary_changed = False
        self.profile_ids = {"bvi": "bvi-v0", "bvi-permissive": "bvi-permissive",
                            "wheelchair": "wheelchair-v0"}
        for slug, code, name, _ in self.expected:
            rel = (self.paths["countries"] / "input_manifests" / slug / "source.json"
                   ).relative_to(FIXTURE_ROOT).as_posix()
            self.provenance["inputs"].append(
                {"country": name, "immutable_manifest": {"path": rel, "sha256": "record"}})
            self.countries.append({
                "country": name, "code": code, "model_version": CORRECTED_VERSION,
                "provenance_status": "manifest verified",
                "jar_sha256": "corrected-jar", "profile_sha256": "bvi-profile",
                "pbf_sha256": "pbf", "graph_scope": "extended",
                "input_manifest_path": rel, "input_manifest_sha256": "record",
                "pess_usable_pct": "20", "opt_usable_pct": "80",
                "drivable_km": "100", "intersections": "50", "crossings": "10",
                "tactile_pct": "5", "name_pct": "20", "sidewalk_pct": "20",
                "bpri_v1": "0.5", "signal_sound_stated": "3",
                "signal_sound_affirmative": "2", "crossing_sound_stated": "1",
                "crossing_conflicts": "0",
            })
        for pid, profile_id in self.profile_ids.items():
            self.profiles.append({
                "profile": pid, "profile_id": profile_id, "unit": "NATIONAL",
                "network_scope": "full", "graph_scope": "extended",
                "min_usable_component_km": "1", "scenario": f"{pid}-full-t1",
                "run_directory": pid, "jar_sha256": "corrected-jar",
                "profile_sha256": "profile", "eligible_km": "100",
                "pess_eligible_usable_km": "20", "opt_eligible_usable_km": "80",
                "pess_usable_pct": "20", "opt_usable_pct": "80",
            })

    def load(self, path):
        path = Path(path)
        if path.name == "cross_country.csv":
            return deepcopy(self.countries)
        if path.name == "cross_country_correlations.csv":
            return [{"n": "20"}]
        if path.name == "profile_sensitivity.csv":
            return deepcopy(self.profiles)
        if path.name == "summary.csv":
            row = deepcopy(self.summary)
            if self.country_summary_changed and path.parent == self.paths["countries"] / "greece":
                row["name_pct"] = "19"
            return [row]
        if path.name == "crossing_metrics.csv":
            return [deepcopy(self.crossings)]
        if path.name == "feasibility.csv":
            row = deepcopy(self.national)
            if path.parent.parent == self.paths["profiles"]:
                row["profile"] = self.profile_ids[path.parent.name]
            return [row]
        raise AssertionError(f"Unexpected fixture CSV: {path}")

    def json_file(self, path):
        path = Path(path)
        if path.name == "input_provenance.json":
            return deepcopy(self.provenance)
        if path.name == "source.json":
            return {"content": {"sha256": "pbf"}}
        if path.name.startswith("profile-") and path.suffix == ".json":
            return {"id": self.profile_ids[path.stem[8:]]}
        if path.name == "profile.json":
            return {"id": self.profile_ids[path.parent.name]}
        raise AssertionError(f"Unexpected fixture JSON: {path}")

    def fake_run(self, paths, directory, pbf, profile, strata, greek, outputs):
        return {"inputs": {"pbf": {"sha256": "pbf"}, "profile": {"sha256": "profile"}},
                "outputs": {"feasibility.csv": {"sha256": "run"}}}

    def verify(self):
        with (patch("paper_data.load", side_effect=self.load),
              patch("paper_data._json_file", side_effect=self.json_file),
              patch("paper_data._verified_run", side_effect=self.fake_run),
              patch("paper_data.digest", return_value="record")):
            return paper_data.verify_unified_inputs(self.paths, self.greek)

    def test_complete_uniform_fixture_passes_without_reading_real_results(self):
        countries, profiles = self.verify()
        self.assertEqual(len(countries), 20)
        self.assertEqual({r["profile"] for r in profiles}, set(self.profile_ids))

    def test_missing_or_mixed_corrected_inputs_fail_before_assembly(self):
        for kind in ("missing_country", "old_jar", "missing_profile", "stale_country_aggregate"):
            with self.subTest(kind=kind):
                original = deepcopy((self.countries, self.profiles))
                try:
                    if kind == "missing_country":
                        self.countries.pop()
                    elif kind == "old_jar":
                        self.countries[0]["jar_sha256"] = "old-jar"
                    elif kind == "missing_profile":
                        self.profiles.pop()
                    else:
                        self.countries[0]["bpri_v1"] = "0.9"
                    with self.assertRaisesRegex(ValueError, "partial|mixed|differs"):
                        self.verify()
                finally:
                    self.countries, self.profiles = original

    def test_greek_country_must_match_corrected_full_strata(self):
        self.country_summary_changed = True
        with self.assertRaisesRegex(ValueError, "country aggregate differs"):
            self.verify()


class CorrectedExecutionFixtureTests(unittest.TestCase):
    def setUp(self):
        self.paths = aggregate.reporting_paths(FIXTURE_ROOT, versioned=True)
        self.directory = self.paths["countries"] / "greece"
        self.pbf = FIXTURE_ROOT / "data" / "greece-latest.osm.pbf"
        self.strata = FIXTURE_ROOT / "data" / "strata_national.json"
        self.profile = FIXTURE_ROOT / "data" / "profile-bvi.json"
        self.jar = FIXTURE_ROOT / "stage" / "target" / "census.jar"
        self.greek = {
            "command": ["java", "-Xmx5g", "-jar", str(self.jar)],
            "input_sha256": {"jar": "jar"},
            "source_sha256": {"Main.java": "source"},
        }
        self.strata_config = {"polygonLayers": []}
        self.checked = []
        self.manifest = {
            "status": "complete",
            "command": ["java", "-Xmx5g", "-jar", str(self.jar), str(self.pbf),
                        str(self.strata), str(self.directory), f"--profile={self.profile}"],
            "inputs": {"jar": {"sha256": "jar"}, "pbf": {"sha256": "pbf"},
                       "strata": {"sha256": "strata"}, "profile": {"sha256": "profile"},
                       "strata_assets": {}, "java_sources": {"Main.java": "source"},
                       "analysis_drivers": {}},
            "outputs": {name: {"sha256": "output"} for name in
                        ("summary.csv", "crossing_metrics.csv",
                         "feasibility.csv", "feasibility_global.csv")},
        }

    def verified(self, version=CORRECTED_VERSION):
        def load(path):
            return [{"stratum": "NATIONAL", "model_version": version}]
        with (patch("paper_data._json_file", side_effect=lambda path: (
                  self.strata_config if Path(path) == self.strata else self.manifest)),
              patch("paper_data._verified_fingerprint",
                    side_effect=lambda record, path: self.checked.append(Path(path))),
              patch("paper_data.load", side_effect=load)):
            return paper_data._verified_run(
                self.paths, self.directory, self.pbf, self.profile, self.strata, self.greek,
                tuple(self.manifest["outputs"]))

    def test_corrected_command_outputs_and_model_are_required(self):
        self.assertIs(self.verified(), self.manifest)
        self.manifest["command"][3] = str(FIXTURE_ROOT / "census" / "target" / "census.jar")
        with self.assertRaisesRegex(ValueError, "inconsistent inputs"):
            self.verified()
        self.manifest["command"][3] = str(self.jar)
        with self.assertRaisesRegex(ValueError, "mixed corrected model versions"):
            self.verified("conditional-2026-09-15")

    def test_incomplete_strata_asset_identity_is_rejected(self):
        self.manifest["inputs"]["strata_assets"] = {"unexpected": {"sha256": "old"}}
        with self.assertRaisesRegex(ValueError, "strata assets are incomplete"):
            self.verified()

    def test_full_strata_asset_fingerprint_is_checked(self):
        asset = FIXTURE_ROOT / "data" / "boundaries" / "fixture.geojson"
        self.strata_config = {"polygonLayers": [{"id": "fixture", "file": "boundaries/fixture.geojson"}]}
        self.manifest["inputs"]["strata_assets"] = {"fixture": {"sha256": "asset"}}
        self.verified()
        self.assertIn(asset, self.checked)

    def test_versioned_derivatives_require_frozen_scope_agreement(self):
        old = {"top_n": 50, "dimos_codes": ["0123"], "laus": ["LAU:fixture"]}
        with (patch("paper_data.verify_versioned_manifest") as verified,
              patch("paper_data._json_file", return_value=old)):
            paper_data.verify_unified_derivatives(self.paths)
        self.assertEqual(verified.call_count, 5)
        mismatched = {**old, "laus": ["LAU:other"]}
        with (patch("paper_data.verify_versioned_manifest"),
              patch("paper_data._json_file", side_effect=[old, mismatched])):
            with self.assertRaisesRegex(ValueError, "frozen audit/Mapillary scope"):
                paper_data.verify_unified_derivatives(self.paths)


class CorrectedDistanceFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = FIXTURE_ROOT
        self.summary = {
            "model_version": CORRECTED_VERSION,
            "intervention_model": targeting_distance.INTERVENTION_MODEL,
            "selection_method": "static_initial_consolidation",
            "component_threshold_km": "1", "usable_km_before": "1",
            "edits_committed": "2", "profile": "bvi-v0", "graph_scope": "extended",
            "usable_gain_km": "2", "survey_km": "2",
            "length_baseline_usable_gain_km": "2", "length_baseline_survey_km": "2",
            "efficiency_usable_gain_km": "2", "efficiency_survey_km": "2",
        }

    def source(self, path):
        if Path(path).name == "edit_targets_summary.csv":
            return [self.summary]
        for key, (name, mode, _) in targeting_distance.SOURCES.items():
            if name != Path(path).name:
                continue
            return [{
                "rank": str(rank), "osm_way_id": str(rank), "model_version": CORRECTED_VERSION,
                "selection_method": key,
                "own_km": "1", "survey_km": "1", "cumulative_survey_km": str(rank),
                "cumulative_usable_gain_km": str(rank),
                "marginal_usable_km": "1", "usable_km_after": str(rank + 1),
                "intervention_model": targeting_distance.INTERVENTION_MODEL,
            } for rank in (1, 2)]
        raise AssertionError(f"Unexpected fixture input: {path}")

    def test_distance_report_uses_corrected_worklists_and_versioned_source_paths(self):
        with (patch("targeting_distance.verify_corrected_greek"),
              patch("targeting_distance.read_csv", side_effect=self.source),
              patch("pathlib.Path.read_bytes", return_value=b"fixture")):
            report, curves = targeting_distance.analyze_sources(self.root, versioned=True)
        self.assertEqual(report["model_version"], CORRECTED_VERSION)
        self.assertEqual(report["common_max_survey_km"], 2)
        self.assertEqual(curves["static_initial_consolidation"].points[-1].tasks, 2)
        self.assertTrue(all(
            item["path"].replace("\\", "/").startswith(
                "results/impact/greece-full-strata-conditional-2026-10-09/")
            for item in report["source_artifacts"].values()))
        self.summary["model_version"] = "conditional-2026-09-15"
        with (patch("targeting_distance.verify_corrected_greek"),
              patch("targeting_distance.read_csv", side_effect=self.source)):
            with self.assertRaisesRegex(ValueError, "model version mismatch"):
                targeting_distance.analyze_sources(self.root, versioned=True)


class BundleSafetyTests(unittest.TestCase):
    def test_optional_manual_exports_must_be_unlinked_project_files(self):
        paths = aggregate.reporting_paths(FIXTURE_ROOT, versioned=True)
        audit = FIXTURE_ROOT / "validation" / "fixtures" / "versioned" / "audit.csv"
        with patch("pathlib.Path.is_file", return_value=True):
            self.assertEqual(paper_data._manual_input_paths(paths, [audit]), [audit])
            with self.assertRaisesRegex(ValueError, "unlinked project file"):
                paper_data._manual_input_paths(paths, [FIXTURE_ROOT.parent / "audit.csv"])
            with self.assertRaisesRegex(ValueError, "unlinked project file"):
                paper_data._manual_input_paths(paths, ["../audit.csv"])

    def test_provenance_includes_corrected_greek_outputs_and_reporting_code(self):
        paths = aggregate.reporting_paths(FIXTURE_ROOT, versioned=True)
        outputs = {"summary.csv": "summary", "feasibility.csv": "feasibility"}
        with (patch("paper_data._json_file", return_value={"output_sha256": outputs}),
              patch("paper_data.digest", side_effect=lambda path: Path(path).name),
              patch("paper_data.os.listdir", side_effect=lambda path:
                    ["input.json"] if "input_manifests" in str(path)
                    else ["pick_933896235308839_fixture.jpg"])):
            hashes = paper_data.reporting_source_hashes(paths)
        self.assertEqual(
            hashes[(paths["census"] / "summary.csv").relative_to(FIXTURE_ROOT).as_posix()],
            "summary.csv")
        self.assertEqual(
            hashes[(paths["census"] / "feasibility.csv").relative_to(FIXTURE_ROOT).as_posix()],
            "feasibility.csv")
        self.assertEqual(hashes["analysis/paper_data.py"], "paper_data.py")
        with patch("paper_data._json_file",
                   return_value={"output_sha256": {"../frozen.csv": "unexpected"}}):
            with self.assertRaisesRegex(ValueError, "Unsafe corrected Greek output"):
                paper_data.reporting_source_hashes(paths)

    def test_bundle_model_and_source_identity_are_required(self):
        paths = aggregate.reporting_paths(FIXTURE_ROOT, versioned=True)
        bundle = {"reporting": {"mode": "versioned-corrected",
                                "model_version": CORRECTED_VERSION,
                                "source_sha256": {"input": "unchanged"}},
                  "national": {"model_version": CORRECTED_VERSION},
                  "feasibility_national": {"model_version": CORRECTED_VERSION}}
        with patch("paper_data.reporting_source_hashes", return_value={"input": "unchanged"}):
            self.assertIs(paper_data.verify_reporting_bundle_sources(paths, bundle), bundle)
        with patch("paper_data.reporting_source_hashes", return_value={"input": "modified"}):
            with self.assertRaisesRegex(ValueError, "sources changed"):
                paper_data.verify_reporting_bundle_sources(paths, bundle)
        bundle["feasibility_national"]["model_version"] = "conditional-2026-09-15"
        with self.assertRaisesRegex(ValueError, "Not a unified"):
            paper_data.verify_reporting_bundle_sources(paths, bundle)
        bundle["feasibility_national"]["model_version"] = CORRECTED_VERSION
        bundle["manual_audit"] = {"available": True}
        with self.assertRaisesRegex(ValueError, "sources changed"):
            paper_data.verify_reporting_bundle_sources(paths, bundle)


class FigureRouteFixtureTests(unittest.TestCase):
    def test_city_figure_reads_the_selected_bundle_not_the_frozen_urban_table(self):
        import figures

        def stop_before_render(path, **kwargs):
            raise OSError(f"Fixture read only: {path}")

        with (patch.object(figures, "ROOT", str(FIXTURE_ROOT)),
              patch.object(figures, "VERSIONED", True),
              patch("figures.io.open", side_effect=stop_before_render) as read):
            with self.assertRaisesRegex(OSError, "Fixture read only"):
                figures.fig_cities()
        self.assertEqual(Path(read.call_args.args[0]),
                         aggregate.reporting_paths(FIXTURE_ROOT, True)["urban"] / "top_cities.csv")
        with (patch.object(figures, "ROOT", str(FIXTURE_ROOT)),
              patch.object(figures, "VERSIONED", False),
              patch("figures.io.open", side_effect=stop_before_render) as read):
            with self.assertRaisesRegex(OSError, "Fixture read only"):
                figures.fig_cities()
        self.assertEqual(Path(read.call_args.args[0]),
                         aggregate.reporting_paths(FIXTURE_ROOT)["urban"] / "top_cities.csv")


if __name__ == "__main__":
    unittest.main()
