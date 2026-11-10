"""Immutable source fixtures and the complete original byte/mode preservation set."""

import hashlib
import json
import subprocess
import unittest

from helper_support import INTAKE, ROOT, original, source
import repository


class PreservationTests(unittest.TestCase):
    def test_exact_pinned_source_blobs_and_no_gitlinks(self):
        fixture = json.loads((ROOT / "tests/fixtures/intake.json").read_text())
        self.assertEqual(fixture["sha"], "014963d3c6c3ab23a8406b108fed3dee27836812")
        selected = repository.entries(subprocess.check_output(
            ["git", "ls-tree", "-r", "-z", INTAKE], cwd=ROOT, text=True))
        for path, data in fixture["source"].items():
            raw = original(path)
            self.assertEqual(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                             data["blob"])
            self.assertEqual(selected[path], (data["mode"], data["blob"]))
        self.assertEqual(len(selected), 86)
        self.assertFalse(any(mode == "160000" for mode, _ in selected.values()))

    def test_every_original_unrelated_path_and_mode_is_raw_identical(self):
        selected = repository.entries(subprocess.check_output(
            ["git", "ls-tree", "-r", "-z", INTAKE], cwd=ROOT, text=True))
        allowed = {*map(str, source.SOURCE_PATHS), ".github/workflows/release.yml",
                   ".github/workflows/bump-version.yml", "README.md"}
        for path, expected in selected.items():
            if path not in allowed:
                self.assertEqual(repository.actual_blob(ROOT / path), expected, path)
        self.assertEqual((ROOT / "Info.plist").read_bytes(), original("Info.plist"))

    def test_authorized_tokens_only_and_localized_history_is_separate_from_buggy_header(self):
        version = source.read(ROOT).encode()
        project = original(source.PROJECT).replace(
            b"MARKETING_VERSION = 2.0.3;", b"MARKETING_VERSION = " + version + b";"
        ).replace(b"CURRENT_PROJECT_VERSION = 2.0.0;",
                  b"CURRENT_PROJECT_VERSION = " + version + b";")
        self.assertEqual((ROOT / source.PROJECT).read_bytes(), project)
        localized = original(source.LOCALIZED).replace(b"2.0.0", version)
        self.assertEqual((ROOT / source.LOCALIZED).read_bytes(), localized)
        self.assertTrue(localized.endswith(b";\r"))
        self.assertEqual(localized.count(b"\r"), 5)
        self.assertNotIn(b"\n", localized)
        workflow = original(".github/workflows/bump-version.yml").decode()
        header, body = workflow.split("name: Bump version", 1)
        self.assertIn("MARKETING_VERSION/CURRENT_PROJECT_VERSION AND Info.plist", header)
        self.assertIn('KEY="MARKETING_VERSION"', body)
        self.assertNotIn("InfoPlist.strings", body)
        for commit in ("fdfc95581f376ba76b978849b3b69253b2a3dbc9",
                       "ee46f7810e2d07effde07bb5b16e83b5fd7ed4dc"):
            names = subprocess.check_output(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
                cwd=ROOT, text=True).splitlines()
            self.assertTrue(any(name.endswith("English.lproj/InfoPlist.strings") for name in names))
