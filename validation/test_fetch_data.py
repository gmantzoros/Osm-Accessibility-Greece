"""Offline PowerShell AST/mock tests; fixtures stay below this project."""

import base64
import hashlib
import json
import shutil
import stat
import subprocess
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "data" / "fetch_data.ps1"
FIXTURES = ROOT / "validation" / "_fetch_fixtures"
POWERSHELL = shutil.which("powershell")
AUX_NAMES = (
    "LAU_RG_01M_2021_4326.geojson",
    "NUTS_RG_01M_2021_4326_LEVL_2.geojson",
    "NUTS_RG_01M_2021_4326_LEVL_3.geojson",
)


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required for offline fetch tests")
class FetchTests(unittest.TestCase):
    def setUp(self):
        self.case = FIXTURES / uuid.uuid4().hex
        self.data = self.case / "data"
        self.data.mkdir(parents=True)
        self.good = b"small frozen fixture, not an actual PBF\n"
        self.md5 = hashlib.md5(self.good).hexdigest()
        self.sha = hashlib.sha256(self.good).hexdigest()
        self.backup = self.case / "verified-backup.bin"
        self.backup.write_bytes(self.good)
        self.pbf = self.data / "greece-latest.osm.pbf"
        self.pbf.write_bytes(self.good)
        self.snapshot = self.data / "SNAPSHOT.txt"
        self.snapshot.write_text(
            "file=greece-latest.osm.pbf\nurl=https://example.invalid/fixture\n"
            "downloaded=2026-08-28 14:05:50 +03:00\n"
            f"md5={self.md5}\nsize_bytes={len(self.good)}\n", encoding="utf-8-sig"
        )
        self.sidecar = self.data / (self.pbf.name + ".md5")
        self.sidecar.write_text(f"{self.md5}  {self.pbf.name}\n", encoding="utf-8")
        self.provenance = self.data / (self.pbf.name + ".provenance.json")
        self.provenance.write_text(json.dumps({
            "path": "data\\greece-latest.osm.pbf",
            "content": {"bytes": len(self.good), "sha256": self.sha, "md5": self.md5},
            "source_status": "synthetic fixture only",
        }), encoding="utf-8")
        self.original_records = self.records()

    def tearDown(self):
        if self.case.parent != FIXTURES:
            raise AssertionError("Fixture cleanup escaped its known directory")
        shutil.rmtree(self.case)
        if FIXTURES.exists() and not any(FIXTURES.iterdir()):
            FIXTURES.chmod(stat.S_IRWXU)
            FIXTURES.rmdir()

    def records(self):
        return {path.name: path.read_bytes() for path in (self.snapshot, self.sidecar, self.provenance)}

    def ps(self, command):
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded], cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )

    def run_fetch(self, *arguments, directory=None, download=None, failure=False):
        source = self.case / "mock-response.bin"
        source.write_bytes(self.good if download is None else download)
        calls = self.case / "mock-calls.txt"
        args = " ".join(str(arg) if str(arg).startswith("-") else quoted(arg) for arg in arguments)
        # The function shadows the network command in this fresh process.
        command = f"""
$ErrorActionPreference = 'Stop'
function global:Invoke-WebRequest {{
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    Add-Content -LiteralPath {quoted(calls)} -Value $Uri
    [IO.File]::WriteAllBytes($OutFile, [IO.File]::ReadAllBytes({quoted(source)}))
    if (${str(failure).lower()}) {{ throw 'mock download failure' }}
}}
function global:Start-BitsTransfer {{ throw 'BITS must not be invoked' }}
try {{
    & {quoted(SCRIPT)} -DataDirectory {quoted(directory or self.data)} {args}
}} catch {{
    Write-Output $_.Exception.Message
    exit 1
}}
"""
        result = self.ps(command)
        call_count = len(calls.read_text().splitlines()) if calls.exists() else 0
        return result, call_count

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(self.data.glob(".fetch-stage-*")))

    def test_powershell_ast_is_valid_without_execution(self):
        result = self.ps(f"""
$tokens = $null
$errors = $null
$null = [Management.Automation.Language.Parser]::ParseFile({quoted(SCRIPT)}, [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ $errors | ForEach-Object {{ Write-Output $_.Message }}; exit 1 }}
Write-Output 'AST valid'
""")
        self.assert_success(result)
        self.assertIn("AST valid", result.stdout)

    def test_default_verifies_offline_and_preserves_records(self):
        result, count = self.run_fetch(failure=True)
        self.assert_success(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.records(), self.original_records)
        self.assertEqual(self.pbf.read_bytes(), self.good)

    def test_default_mismatch_fails_without_network_or_replacement(self):
        self.pbf.write_bytes(b"changed fixture")
        result, count = self.run_fetch()
        self.assert_failure(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.pbf.read_bytes(), b"changed fixture")
        self.assertEqual(self.records(), self.original_records)

    def test_recorded_sha_mismatch_fails_without_network(self):
        record = json.loads(self.provenance.read_text(encoding="utf-8"))
        record["content"]["sha256"] = "0" * 64
        self.provenance.write_text(json.dumps(record), encoding="utf-8")
        before = self.records()
        result, count = self.run_fetch()
        self.assert_failure(result)
        self.assertIn("checksum/size mismatch", result.stdout)
        self.assertEqual(count, 0)
        self.assertEqual(self.records(), before)
        self.assertEqual(self.pbf.read_bytes(), self.good)

    def test_verify_refuses_acquisition_options(self):
        result, count = self.run_fetch("-SourceUrl", "https://example.invalid/extract")
        self.assert_failure(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.records(), self.original_records)

    def test_restore_verified_existing_never_downloads(self):
        result, count = self.run_fetch("-Mode", "Restore", "-SourceUrl",
                                       "https://example.invalid/new-latest", failure=True)
        self.assert_success(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.records(), self.original_records)

    def test_restore_missing_from_explicit_local_backup(self):
        self.pbf.unlink()
        result, count = self.run_fetch("-Mode", "Restore", "-RestoreFrom", self.backup)
        self.assert_success(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.pbf.read_bytes(), self.good)
        self.assertEqual(self.records(), self.original_records)

    def test_restore_verified_mock_download_atomically_replaces_bad_bytes(self):
        self.pbf.write_bytes(b"wrong prior fixture")
        result, count = self.run_fetch("-Mode", "Restore", "-SourceUrl", "https://example.invalid/archive")
        self.assert_success(result)
        self.assertEqual(count, 1)
        self.assertEqual(self.pbf.read_bytes(), self.good)
        self.assertEqual(self.records(), self.original_records)
        self.assertFalse(any(self.data.glob(".fetch-stage-*")))

    def test_download_checksum_failure_preserves_input_and_records(self):
        self.pbf.write_bytes(b"prior fixture to preserve")
        result, count = self.run_fetch("-Mode", "Restore", "-SourceUrl",
                                       "https://example.invalid/archive", download=b"x" * len(self.good))
        self.assert_failure(result)
        self.assertEqual(count, 1)
        self.assertEqual(self.pbf.read_bytes(), b"prior fixture to preserve")
        self.assertEqual(self.records(), self.original_records)

    def test_partial_download_failure_is_not_a_warning_success(self):
        self.pbf.write_bytes(b"prior fixture to preserve")
        result, count = self.run_fetch("-Mode", "Restore", "-SourceUrl",
                                       "https://example.invalid/archive", failure=True)
        self.assert_failure(result)
        self.assertEqual(count, 1)
        self.assertEqual(self.pbf.read_bytes(), b"prior fixture to preserve")
        self.assertEqual(self.records(), self.original_records)

    def test_conflicting_recorded_hashes_fail_before_download(self):
        self.sidecar.write_text("0" * 32 + "  " + self.pbf.name, encoding="utf-8")
        before = self.records()
        result, count = self.run_fetch("-Mode", "Restore", "-SourceUrl", "https://example.invalid/archive")
        self.assert_failure(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.records(), before)

    def test_actual_and_common_md5_formats_are_strictly_supported(self):
        formats = [self.md5, f"{self.md5}  {self.pbf.name}",
                   f"{self.md5} *{self.pbf.name}", f"MD5 ({self.pbf.name}) = {self.md5.upper()}"]
        for record in formats:
            with self.subTest(record=record):
                self.sidecar.write_text(record + "\n", encoding="utf-8-sig")
                result, count = self.run_fetch()
                self.assert_success(result)
                self.assertEqual(count, 0)
        self.sidecar.write_text(self.md5 + "  other-file.pbf\n", encoding="utf-8")
        result, count = self.run_fetch()
        self.assert_failure(result)
        self.assertEqual(count, 0)

    def test_legacy_md5_only_snapshot_is_not_silently_replaced(self):
        self.provenance.unlink()
        result, count = self.run_fetch()
        self.assert_success(result)
        self.assertEqual(count, 0)

    def test_smoke_verification_does_not_use_or_write_greek_snapshot(self):
        malta = self.data / "malta-latest.osm.pbf"
        malta.write_bytes(self.good)
        (self.data / (malta.name + ".md5")).write_text(self.md5 + "  " + malta.name, encoding="utf-8")
        result, count = self.run_fetch("-Smoke")
        self.assert_success(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.records(), self.original_records)
        self.assertFalse((self.data / "SNAPSHOT-malta.txt").exists())

    def test_update_refuses_existing_snapshot_and_unacknowledged_destination(self):
        result, count = self.run_fetch("-Mode", "Update", "-ConfirmNewSnapshot",
                                       "-ExpectedSha256", self.sha, "-SourceUrl",
                                       "https://example.invalid/new")
        self.assert_failure(result)
        self.assertEqual(count, 0)
        self.assertEqual(self.records(), self.original_records)
        result, count = self.run_fetch("-Mode", "Update", "-ExpectedSha256", self.sha,
                                       directory=self.case / "new")
        self.assert_failure(result)
        self.assertEqual(count, 0)

    def test_explicit_new_snapshot_is_separate_and_does_not_invent_header_time(self):
        destination = self.case / "new"
        result, count = self.run_fetch("-Mode", "Update", "-ConfirmNewSnapshot",
                                       "-ExpectedSha256", self.sha, "-SourceUrl",
                                       "https://example.invalid/new", directory=destination)
        self.assert_success(result)
        self.assertEqual(count, 1)
        self.assertEqual((destination / self.pbf.name).read_bytes(), self.good)
        record = (destination / "SNAPSHOT.txt").read_text(encoding="utf-8")
        self.assertIn("sha256=" + self.sha, record)
        self.assertIn("snapshot_timestamp_status=OSMHeader-not-decoded", record)
        self.assertEqual(self.records(), self.original_records)
        self.assertEqual(self.pbf.read_bytes(), self.good)

    def test_failed_new_snapshot_leaves_pinned_snapshot_untouched(self):
        destination = self.case / "new"
        result, count = self.run_fetch("-Mode", "Update", "-ConfirmNewSnapshot",
                                       "-ExpectedSha256", self.sha, "-SourceUrl",
                                       "https://example.invalid/new", directory=destination,
                                       download=b"mismatch")
        self.assert_failure(result)
        self.assertEqual(count, 1)
        self.assertFalse((destination / self.pbf.name).exists())
        self.assertFalse((destination / "SNAPSHOT.txt").exists())
        self.assertEqual(self.records(), self.original_records)
        self.assertFalse(any(destination.glob(".fetch-stage-*")))

    def test_new_snapshot_rejects_credential_urls_without_download(self):
        for url in ("https://example.invalid/new?api%5Fkey=synthetic",
                    "https://example.invalid/new#token=synthetic",
                    "https://" + "user:synthetic@example.invalid/new"):
            with self.subTest(url=url):
                destination = self.case / "new"
                result, count = self.run_fetch("-Mode", "Update", "-ConfirmNewSnapshot",
                                               "-ExpectedSha256", self.sha, "-SourceUrl",
                                               url, directory=destination)
                self.assert_failure(result)
                self.assertEqual(count, 0)
                self.assertFalse((destination / self.pbf.name).exists())
                self.assertEqual(self.records(), self.original_records)

    def auxiliary(self, modern=True):
        directory = self.data / "external"
        directory.mkdir()
        lines = ["# Synthetic fixture: no real acquisition"]
        for name in AUX_NAMES:
            (directory / name).write_bytes(self.good)
            key, digest = ("sha256", self.sha) if modern else ("md5", self.md5)
            url_field = "declared_source_url" if modern else "url"
            lines.append(f"file={name} {url_field}=https://example.invalid/{name} "
                         f"{key}={digest} size_bytes={len(self.good)}")
        manifest = directory / "AUX_SNAPSHOT.txt"
        manifest.write_text("\n".join(lines), encoding="utf-8")
        return directory, manifest

    def test_current_sha_and_legacy_auxiliary_inventory_formats(self):
        for modern in (True, False):
            with self.subTest(modern=modern):
                directory, manifest = self.auxiliary(modern)
                before = manifest.read_bytes()
                result, count = self.run_fetch("-Aux")
                self.assert_success(result)
                self.assertEqual(count, 0)
                self.assertEqual(manifest.read_bytes(), before)
                shutil.rmtree(directory)
        self.assertEqual(self.records(), self.original_records)

    def test_auxiliary_restore_failure_preserves_verified_existing_snapshot(self):
        directory, manifest = self.auxiliary()
        before = manifest.read_bytes()
        (directory / AUX_NAMES[1]).unlink()
        result, count = self.run_fetch("-Aux", "-Mode", "Restore", "-UseRecordedAuxiliaryUrls",
                                       download=b"wrong auxiliary response")
        self.assert_failure(result)
        self.assertEqual(count, 1)
        self.assertEqual((directory / AUX_NAMES[0]).read_bytes(), self.good)
        self.assertEqual((directory / AUX_NAMES[2]).read_bytes(), self.good)
        self.assertFalse((directory / AUX_NAMES[1]).exists())
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(self.records(), self.original_records)

    def test_auxiliary_checksum_failure_does_not_replace_original_bytes(self):
        directory, manifest = self.auxiliary()
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            f"sha256={self.sha}", "sha256=" + "0" * 64, 1), encoding="utf-8")
        before = manifest.read_bytes()
        result, count = self.run_fetch("-Aux", "-Mode", "Restore", "-UseRecordedAuxiliaryUrls")
        self.assert_failure(result)
        self.assertIn("checksum/size mismatch", result.stdout)
        self.assertEqual(count, 1)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertTrue(all((directory / name).read_bytes() == self.good for name in AUX_NAMES))
        self.assertEqual(self.records(), self.original_records)

    def test_auxiliary_restore_refuses_unpinned_manifest_and_pbf_url(self):
        directory, manifest = self.auxiliary()
        before = manifest.read_bytes()
        for arguments in (
            ("-Aux", "-Mode", "Restore", "-AuxManifestPath", manifest,
             "-UseRecordedAuxiliaryUrls"),
            ("-Aux", "-Mode", "Restore", "-SourceUrl", "https://example.invalid/ignored"),
        ):
            with self.subTest(arguments=arguments):
                result, count = self.run_fetch(*arguments)
                self.assert_failure(result)
                self.assertEqual(count, 0)
                self.assertEqual(manifest.read_bytes(), before)
                self.assertEqual(self.records(), self.original_records)

    def test_auxiliary_update_requires_supplied_checksums_and_separate_destination(self):
        directory, manifest = self.auxiliary()
        before = manifest.read_bytes()
        destination = self.case / "new"
        result, count = self.run_fetch("-Aux", "-Mode", "Update", "-ConfirmNewSnapshot",
                                       "-AuxManifestPath", manifest, directory=destination)
        self.assert_success(result)
        self.assertEqual(count, len(AUX_NAMES))
        self.assertTrue(all((destination / "external" / name).read_bytes() == self.good
                            for name in AUX_NAMES))
        self.assertEqual((destination / "external" / "AUX_SNAPSHOT.txt").read_bytes(), before)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertTrue(all((directory / name).read_bytes() == self.good for name in AUX_NAMES))
        self.assertEqual(self.records(), self.original_records)

    def test_auxiliary_update_bad_checksum_fails_closed(self):
        _, manifest = self.auxiliary()
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            f"sha256={self.sha}", "sha256=" + "0" * 64, 1), encoding="utf-8")
        before = manifest.read_bytes()
        destination = self.case / "new"
        result, count = self.run_fetch("-Aux", "-Mode", "Update", "-ConfirmNewSnapshot",
                                       "-AuxManifestPath", manifest, directory=destination)
        self.assert_failure(result)
        self.assertEqual(count, 1)
        self.assertFalse((destination / "external" / AUX_NAMES[0]).exists())
        self.assertFalse((destination / "external" / "AUX_SNAPSHOT.txt").exists())
        self.assertFalse(any(destination.glob(".fetch-stage-*")))
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(self.records(), self.original_records)

    def test_auxiliary_inventory_path_escape_is_rejected(self):
        _, manifest = self.auxiliary()
        manifest.write_text(f"file=..\\escape.geojson md5={self.md5}", encoding="utf-8")
        result, count = self.run_fetch("-Aux", "-Mode", "Restore", "-UseRecordedAuxiliaryUrls")
        self.assert_failure(result)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
