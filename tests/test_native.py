"""Physical cleanup probes and exact generated boundary regressions."""

import errno
import os
from pathlib import Path
from unittest.mock import patch

from helper_support import Fixture, GitFixture, cwd
import native
import repository


class PhysicalProbeTests(Fixture):
    def test_only_enoent_is_absence_each_enclosing_marker_is_lstat_probed(self):
        for path in native.FILES:
            self.write(path, b"stale")
        self.write(native.DIRECTORIES[0] / "nested/last.o", b"intermediate")
        before = {path: (self.root / path).read_bytes() for path in native.FILES}
        real = Path.lstat
        marker = Path("test/Plugins/.git")
        observed = []
        def inspect(path, *args, **kwargs):
            observed.append(path)
            return real(path, *args, **kwargs)
        with cwd(self.root), patch.object(Path, "lstat", inspect):
            native.validate_paths((*native.FILES, *native.DIRECTORIES))
        for enclosing in ("test/.git", "test/Plugins/.git",
                          str(native.BUNDLE / ".git"),
                          str(native.BUNDLE / "Contents/.git"),
                          str(native.DIRECTORIES[0] / "nested/.git")):
            self.assertIn(Path(enclosing), observed)
        for number in (errno.EACCES, errno.EIO, errno.ENOTDIR):
            def inaccessible(path, *args, **kwargs):
                if path == marker:
                    raise OSError(number, "fixture marker lstat failure")
                return real(path, *args, **kwargs)
            with cwd(self.root), patch.object(Path, "lstat", inaccessible):
                with self.assertRaises(OSError) as raised:
                    native.prepare()
                self.assertEqual(raised.exception.errno, number)
            self.assertEqual(before, {path: (self.root / path).read_bytes() for path in before})
            with cwd(self.root):
                native.prepare()
            for path, data in before.items():
                self.write(path, data)
            self.write(native.DIRECTORIES[0] / "nested/last.o", b"intermediate")

    def test_access_denial_and_late_target_probe_errors_precede_all_deletion(self):
        for path in native.FILES:
            self.write(path, b"stale")
        before = {path: (self.root / path).read_bytes() for path in native.FILES}
        real_access = os.access
        for denied in (Path("test/Plugins"), native.FILES[-1], native.FILES[-1].parent):
            def access(path, mode):
                return False if path == denied else real_access(path, mode)
            with cwd(self.root), patch.object(os, "access", access):
                with self.assertRaises(ValueError):
                    native.prepare()
            self.assertEqual(before, {path: (self.root / path).read_bytes() for path in before})
            with cwd(self.root):
                native.prepare()
            for path, data in before.items():
                self.write(path, data)
        real_stat = Path.lstat
        for number in (errno.EACCES, errno.EIO, errno.ENOTDIR):
            def inspect(path, *args, **kwargs):
                if path == native.FILES[-1]:
                    raise OSError(number, "late target probe")
                return real_stat(path, *args, **kwargs)
            with cwd(self.root), patch.object(Path, "lstat", inspect), self.assertRaises(OSError):
                native.prepare()
            self.assertEqual(before, {path: (self.root / path).read_bytes() for path in before})
            with cwd(self.root):
                native.prepare()
            for path, data in before.items():
                self.write(path, data)

    def test_exact_default_classifier_does_not_invent_objroot_or_default_package(self):
        for name in ("build.db", "build.db-wal", "build.db-shm",
                     "0123456789abcdef0123456789abcdef.xcbuilddata/build-request.json"):
            self.assertTrue(native.generated("test/Plugins/XCBuildData/" + name))
        self.assertTrue(native.generated("test/Plugins/Continuity Camera.build/Default/main.o"))
        for name in (
            "test/Plugins/XCBuildData/unexpected-source.cpp",
            "test/Plugins/XCBuildData/build.db-evil",
            "test/Plugins/XCBuildData/evil.xcbuilddata/build-request.json",
            "test/Plugins/XCBuildData-evil/build.db",
            "test/Plugins/Continuity Camera.build-evil/Default/main.o",
            "test/Plugins/Default/Continuity Camera.bundle/Contents/Info.plist",
            "test/Plugins/Default/Continuity Camera.build/main.o",
            "build/Continuity Camera.build/main.o", "build/XCBuildData/build.db",
            "test/Plugins/SwiftExplicitPrecompiledModules/injected.cpp",
            "test/Plugins/ExplicitPrecompiledModules/injected.cpp",
            "test/Plugins/EagerLinkingTBDs/Default/injected.cpp",
        ):
            self.assertFalse(native.generated(name), name)


class TrackedIntermediateTests(GitFixture):
    def test_selected_or_indexed_intermediate_sources_never_cleaned_or_finally_exempted(self):
        path = native.DIRECTORIES[0] / "historical.cpp"
        target = self.local / path
        target.parent.mkdir(parents=True)
        target.write_bytes(b"selected source")
        self.git("add", "--", str(path))
        for committed in (False, True):
            if committed:
                self.git("commit", "--quiet", "-m", "historical source under output root")
                self.sha = self.git("rev-parse", "HEAD")
            first = self.local / native.FILES[0]
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"must remain")
            with cwd(self.local), self.assertRaises(ValueError):
                native.prepare()
            self.assertEqual(first.read_bytes(), b"must remain")
            self.assertEqual(target.read_bytes(), b"selected source")
            with cwd(self.local), self.assertRaises(ValueError):
                repository.verify_checkout(self.sha, generated=native.generated, final=True)
