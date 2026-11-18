"""Isolated execution-manifest fixtures; never invoke a real Java census."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import run_corrected_greece as runner


class CorrectedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.stage = base / "build" / "census"
        self.jar = self.stage / "target/census.jar"
        self.output = self.root / runner.IMPACT_DIR
        self.write(self.root / "census/target/census.jar", b"frozen fixture JAR")
        self.write(self.root / "census/pom.xml", b"fixture descriptor")
        self.write(self.stage / "pom.xml", b"fixture descriptor")
        self.write(self.jar, b"new fixture JAR")
        self.write(self.root / "data/greece-latest.osm.pbf", b"pinned fixture extract")
        self.write(self.root / "data/strata.json", b"fixture strata")
        self.write(self.root / "data/profile-bvi.json", b"fixture profile")
        for index in range(21):
            name = f"Fixture{index}.java"
            self.write(self.root / "census/src/main/java" / name, b"class Fixture {}\n")
            self.write(self.stage / "src/main/java" / name, b"class Fixture {}\n")
        for name, contents in (
                ("FROZEN_JAR_SHA256", b"frozen fixture JAR"),
                ("GREEK_PBF_SHA256", b"pinned fixture extract")):
            override = patch.object(runner, name, hashlib.sha256(contents).hexdigest())
            override.start()
            self.addCleanup(override.stop)

    @staticmethod
    def write(path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    def fake_run(self, command, **kwargs):
        self.assertEqual(kwargs, {"cwd": self.root.resolve(), "check": True})
        self.assertNotIn("--sample", command)
        self.assertIn("--core-sensitivity", command)
        self.output.mkdir(parents=True)
        for name in runner.COMPARISON_FILES:
            contents = b"fixture output\n"
            if name == "feasibility.csv":
                contents = (b"stratum,model_version,profile,graph_scope,component_threshold_km\n"
                            b"NATIONAL,conditional-2026-10-09,bvi-v0,extended,1.000\n")
            self.write(self.output / name, contents)

    def test_manifest_links_actual_successful_execution(self):
        with patch.object(runner.subprocess, "run", side_effect=self.fake_run) as invoked:
            recorded = runner.run(self.root, self.jar)
        invoked.assert_called_once()
        saved = json.loads((self.output / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, recorded)
        self.assertEqual(recorded["status"], "complete")
        self.assertEqual(recorded["model_version"], runner.CORRECTED_VERSION)
        self.assertEqual(set(recorded["output_sha256"]), set(runner.COMPARISON_FILES))
        self.assertEqual(len(recorded["source_sha256"]), 21)
        self.assertNotEqual(recorded["input_sha256"]["jar"], runner.FROZEN_JAR_SHA256)

    def test_refuses_existing_output_and_protected_jar(self):
        self.output.mkdir(parents=True)
        with patch.object(runner.subprocess, "run") as invoked:
            with self.assertRaises(FileExistsError):
                runner.run(self.root, self.jar)
            invoked.assert_not_called()
        self.output.rmdir()
        with self.assertRaisesRegex(ValueError, "frozen census JAR"):
            runner.run(self.root, self.root / "census/target/census.jar")

    def test_refuses_different_staged_source(self):
        self.write(self.stage / "src/main/java/Fixture1.java", b"class Changed {}\n")
        with patch.object(runner.subprocess, "run") as invoked:
            with self.assertRaisesRegex(ValueError, "sources differ"):
                runner.run(self.root, self.jar)
            invoked.assert_not_called()

    def test_failed_census_does_not_get_success_manifest(self):
        with patch.object(runner.subprocess, "run", side_effect=subprocess.CalledProcessError(2, "java")):
            with self.assertRaises(subprocess.CalledProcessError):
                runner.run(self.root, self.jar)
        self.assertFalse((self.output / "run_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
