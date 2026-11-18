"""Fixture-only tests for isolated, versioned country and profile reruns."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import cross_country as countries
import profile_sensitivity as profiles
import run_corrected_greece as greek


class UnifiedCorrectedRerunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.stage = base / "build" / "census"
        self.jar = self.stage / "target" / "census.jar"
        self.pbf = self.root / "data" / "greece-latest.osm.pbf"
        self.strata = self.root / "data" / "strata_national.json"
        self.profile = self.root / "data" / "profile-bvi.json"
        self.out = self.root / "results" / "impact" / f"unified-{countries.CORRECTED_VERSION}"
        self.write(self.root / "census/target/census.jar", b"frozen fixture JAR")
        self.write(self.root / "census/pom.xml", b"fixture Maven descriptor")
        self.write(self.stage / "pom.xml", b"fixture Maven descriptor")
        self.write(self.jar, b"corrected fixture JAR")
        self.write(self.pbf, b"cached fixture extract")
        self.write(self.strata, b"{}")
        self.write(self.profile, b'{"id": "bvi-v0", "minUsableComponentKm": 1}')
        for name in ("cross_country.py", "profile_sensitivity.py"):
            self.write(self.root / "analysis" / name, b"fixture analysis driver")
        for index in range(21):
            name = f"Fixture{index}.java"
            self.write(self.root / "census/src/main/java" / name, b"class Fixture {}\n")
            self.write(self.stage / "src/main/java" / name, b"class Fixture {}\n")
        self.patch(countries, "ROOT", str(self.root))
        self.patch(countries, "PROFILE", str(self.profile))
        self.patch(countries, "STRATA", str(self.strata))
        self.patch(greek, "FROZEN_JAR_SHA256", hashlib.sha256(b"frozen fixture JAR").hexdigest())
        self.provenance = {
            "content": countries.fingerprint(self.pbf),
            "osm_header": {"replication_timestamp": None},
        }

    def patch(self, module, name, value):
        override = patch.object(module, name, value)
        override.start()
        self.addCleanup(override.stop)

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def fake_java(self, command, **kwargs):
        self.assertEqual(kwargs, {"capture_output": True, "text": True})
        self.assertEqual(Path(command[3]).resolve(), self.jar.resolve())
        out = Path(command[6])
        self.assertTrue(out.resolve().is_relative_to(self.out.resolve()))
        self.write(
            out / "feasibility.csv",
            b"stratum,model_version,graph_scope\n"
            b"NATIONAL,conditional-2026-10-09,extended\n",
        )
        for name in ("summary.csv", "crossing_metrics.csv", "feasibility_global.csv"):
            self.write(out / name, b"fixture result\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_rejects_frozen_output_jar_and_stale_staged_source(self):
        with self.assertRaisesRegex(ValueError, "both --jar and --output"):
            countries.versioned_model_paths(str(self.jar), None, "countries")
        with self.assertRaisesRegex(ValueError, "unlinked"):
            countries.versioned_model_paths(
                str(self.jar), str(self.root / "results/countries"), "countries"
            )
        with self.assertRaisesRegex(ValueError, "protected"):
            countries.versioned_model_paths(
                str(self.root / "census/target/census.jar"), str(self.out / "countries"), "countries"
            )
        self.write(self.stage / "src/main/java/Fixture1.java", b"class Changed {}\n")
        with self.assertRaisesRegex(ValueError, "sources differ"):
            countries.versioned_model_paths(str(self.jar), str(self.out / "countries"), "countries")

    def test_cached_provenance_does_not_touch_input_sidecar(self):
        sidecar = Path(str(self.pbf) + ".provenance.json")
        self.write(sidecar, json.dumps({
            "content": countries.fingerprint(self.pbf),
            "source_url": "https://example.org/verified-fixture.pbf",
            "downloaded_at_utc": "2026-08-01T00:00:00Z",
        }).encode())
        original = sidecar.read_bytes()
        with patch.object(countries, "pbf_header", return_value={}):
            record = countries.input_provenance(str(self.pbf), persist=False)
        self.assertEqual(sidecar.read_bytes(), original)
        self.assertEqual(record["source_url"], "https://example.org/verified-fixture.pbf")
        sidecar.unlink()
        with patch.object(countries, "pbf_header", return_value={}):
            record = countries.input_provenance(str(self.pbf), persist=False)
        self.assertFalse(sidecar.exists())
        self.assertIsNone(record["source_url"])

    def test_country_manifest_uses_staged_jar_and_versioned_output(self):
        jar, out = countries.versioned_model_paths(
            str(self.jar), str(self.out / "countries"), "countries"
        )
        with patch.object(countries.subprocess, "run", side_effect=self.fake_java) as invoked:
            result, ok = countries.run_census(
                "greece", str(self.pbf), self.provenance, jar=jar, out=out,
                threads=2, expected_version=countries.CORRECTED_VERSION,
            )
            self.assertTrue(ok)
            cached, ok = countries.run_census(
                "greece", str(self.pbf), self.provenance, report_only=True,
                jar=jar, out=out, threads=2, expected_version=countries.CORRECTED_VERSION,
            )
        self.assertEqual(result, cached)
        invoked.assert_called_once()
        saved = json.loads((Path(result) / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["inputs"]["jar"]["sha256"], countries.fingerprint(self.jar)["sha256"])
        self.assertEqual(saved["inputs"]["options"], ["--threads=2"])
        self.assertFalse((self.root / "results/countries").exists())

    def test_profile_manifest_uses_staged_jar_and_versioned_output(self):
        jar, out = countries.versioned_model_paths(
            str(self.jar), str(self.out / "profiles"), "profiles"
        )
        directory = Path(out) / "bvi"
        with patch.object(profiles.subprocess, "run", side_effect=self.fake_java) as invoked:
            self.assertTrue(profiles.run(
                str(self.pbf), str(self.strata), str(self.profile), str(directory),
                None, False, self.provenance, jar=jar,
                expected_version=countries.CORRECTED_VERSION,
            ))
            self.assertTrue(profiles.run(
                str(self.pbf), str(self.strata), str(self.profile), str(directory),
                None, False, self.provenance, report_only=True, jar=jar,
                expected_version=countries.CORRECTED_VERSION,
            ))
        invoked.assert_called_once()
        saved = json.loads((directory / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["inputs"]["jar"]["sha256"], countries.fingerprint(self.jar)["sha256"])
        self.assertFalse((self.root / "results/profile_sensitivity").exists())

    def test_wrong_model_version_never_gets_success_manifest(self):
        def wrong_java(command, **kwargs):
            result = self.fake_java(command, **kwargs)
            self.write(
                Path(command[6]) / "feasibility.csv",
                b"stratum,model_version,graph_scope\n"
                b"NATIONAL,conditional-2026-09-15,extended\n",
            )
            return result

        with patch.object(countries.subprocess, "run", side_effect=wrong_java):
            with self.assertRaisesRegex(ValueError, "model version"):
                countries.run_census(
                    "greece", str(self.pbf), self.provenance, jar=str(self.jar),
                    out=str(self.out / "countries"), expected_version=countries.CORRECTED_VERSION,
                )
        self.assertFalse((self.out / "countries/greece/run_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
