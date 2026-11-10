"""The production migration guard uses one literal SHA, never symbolic HEAD."""

import os
import shutil
import subprocess
import sys

from helper_support import GitFixture, ROOT, source


class MigrationGuardTests(GitFixture):
    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / ".github/migration-hooks", self.local / ".github/migration-hooks")
        self.git("add", "--", ".github/migration-hooks")
        self.git("commit", "--quiet", "-m", "selected migration hook fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.hook = self.local / ".github/migration-hooks/pre-push"
        self.branch = "refs/heads/miyako-simplify-continuity-camera-workflows"

    def call(self, row, old=None, args=()):
        return subprocess.run([str(self.hook), *args], input=row, text=True,
                              cwd=self.local, env={
                                  **self.env, "MIGRATION_HEAD": self.head,
                                  "MIGRATION_OLD": old or "0" * 40},
                              capture_output=True, timeout=30)

    def test_literal_new_branch_zero_old_and_exact_update_only(self):
        zero = "0" * 40
        row = f"{self.head} {self.head} {self.branch} {zero}\n"
        self.assertEqual(self.call(row).returncode, 0)
        self.assertEqual(self.call("", args=("--check",)).returncode, 0)
        for bad in ("", row * 2, row.replace(self.head + " ", "HEAD ", 1),
                    row.replace(self.branch, "refs/heads/main"),
                    row.replace(zero, self.sha), "malformed\n"):
            self.assertNotEqual(self.call(bad).returncode, 0)
        corrected = row.replace(zero, self.sha)
        self.assertEqual(self.call(corrected, old=self.sha).returncode, 0)
        self.assertEqual(self.remote(), self.sha)
        self.assertEqual(self.git("--git-dir=" + str(self.bare), "tag", "--list"), "")

    def test_every_check_rejects_replacement_samebytes_symlink_nonexec_missing_committed_modes(self):
        data = self.hook.read_bytes()
        destination = self.root / "same-bytes-hook"
        destination.write_bytes(data)
        destination.chmod(0o755)
        for kind in ("replacement", "symlink", "nonexec"):
            if kind == "replacement":
                self.hook.write_bytes(data + b"\n")
            elif kind == "symlink":
                self.hook.unlink()
                self.hook.symlink_to(destination)
            else:
                self.hook.chmod(0o644)
            if kind == "nonexec":
                result = subprocess.run(["python3", str(self.hook), "--check"], cwd=self.local,
                                        env={**self.env, "MIGRATION_HEAD": self.head,
                                             "MIGRATION_OLD": "0"*40}, capture_output=True)
            else:
                result = self.call("", args=("--check",))
            self.assertNotEqual(result.returncode, 0)
            self.hook.unlink()
            self.hook.write_bytes(data)
            self.hook.chmod(0o755)
        self.git("update-index", "--chmod=-x", "--", ".github/migration-hooks/pre-push")
        self.git("commit", "--quiet", "-m", "nonexecutable selected mode")
        self.head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.call("", args=("--check",)).returncode, 0)

    def assert_both_paths(self, expected):
        row = f"{self.head} {self.head} {self.branch} {'0' * 40}\n"
        for args, updates in ((("--check",), ""), ((), row)):
            before = {name: ((self.local / name).read_bytes(),
                             (self.local / name).stat().st_mode & 0o777)
                      for name in self.git("ls-files", "-c", "-o", "-z").split("\0") if name}
            state = {key: self.git(*command) for key, command in (
                ("head", ("rev-parse", "HEAD")), ("index", ("ls-files", "--stage", "-z")),
                ("v", ("ls-files", "-v")), ("f", ("ls-files", "-f")),
            )}
            result = self.call(updates, args=args)
            if expected:
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(before, {
                name: ((self.local / name).read_bytes(), (self.local / name).stat().st_mode & 0o777)
                for name in before})
            self.assertEqual(state, {key: self.git(*command) for key, command in (
                ("head", ("rev-parse", "HEAD")), ("index", ("ls-files", "--stage", "-z")),
                ("v", ("ls-files", "-v")), ("f", ("ls-files", "-f")),
            )})

    def test_genuine_missing_wrong_nonending_trailers_both_cli_paths_and_each_restored_positive(self):
        selected = self.head
        tree = self.git("rev-parse", "HEAD^{tree}")
        source_bytes = {path: (self.local / path).read_bytes() for path in source.SOURCE_PATHS}
        index = self.git("ls-files", "--stage", "-z")
        for message in (
            "Missing coauthor",
            "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>\n\nlater text",
            "not a trailer: Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com> ",
        ):
            with self.subTest(message=message):
                result = subprocess.run(
                    ["git", "commit-tree", tree, "-p", self.sha], input=message + "\n",
                    cwd=self.local, env=self.env, text=True, capture_output=True, check=True)
                self.head = result.stdout.strip()
                self.git("reset", "--hard", self.head)
                self.assertEqual(
                    self.git("log", "-1", "--format=%B", check=False).stdout.rstrip("\n"), message)
                try:
                    self.assert_both_paths(False)
                    self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
                    self.assertEqual(self.git("ls-files", "--stage", "-z"), index)
                finally:
                    self.git("reset", "--hard", selected)
                    self.head = selected
                self.assert_both_paths(True)
                self.assertEqual(source_bytes, {
                    path: (self.local / path).read_bytes() for path in source_bytes})

    def test_raw_index_reversed_hidden_v_and_f_and_global_dirt_each_restored_positive(self):
        (self.local / ".gitignore").write_text("prospective-ignored\n")
        self.git("add", "--", ".gitignore")
        self.git("commit", "--quiet", "-m", "prospective ignored fixture")
        self.head = self.git("rev-parse", "HEAD")
        index = self.git("ls-files", "--stage", "-z")
        path = str(source.PROJECT)
        target = self.local / path
        original = target.read_bytes()
        monitor = self.root / "prospective-fsmonitor"
        monitor.write_text(
            f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'prospective-token\\0')\n")
        monitor.chmod(0o755)
        for kind in ("raw", "staged", "reversed", "assume-unchanged", "skip-worktree",
                     "fsmonitor", "untracked", "ignored"):
            with self.subTest(kind=kind):
                unexpected = self.local / (
                    "prospective-ignored" if kind == "ignored" else "prospective-untracked")
                if kind in ("raw", "staged", "reversed"):
                    target.write_bytes(original + b" altered")
                    if kind in ("staged", "reversed"):
                        self.git("add", "--", path)
                    if kind == "reversed":
                        target.write_bytes(original)
                elif kind in ("assume-unchanged", "skip-worktree"):
                    self.git("update-index", "--" + kind, "--", path)
                    self.assertNotIn("H " + path, self.git("ls-files", "-v").splitlines())
                elif kind == "fsmonitor":
                    self.git("config", "core.fsmonitor", str(monitor))
                    self.git("config", "core.fsmonitorHookVersion", "2")
                    self.git("update-index", "--fsmonitor-valid", "--", path)
                    self.assertIn("H " + path, self.git("ls-files", "-v").splitlines())
                    self.assertIn("h " + path, self.git("ls-files", "-f").splitlines())
                else:
                    unexpected.write_bytes(b"unexpected initial source")
                    if kind == "ignored":
                        self.assertEqual(self.git("check-ignore", "--", unexpected.name), unexpected.name)
                try:
                    self.assert_both_paths(False)
                    self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
                finally:
                    if kind in ("assume-unchanged", "skip-worktree"):
                        self.git("update-index", "--no-" + kind, "--", path)
                    elif kind == "fsmonitor":
                        self.git("config", "--unset", "core.fsmonitor")
                        self.git("update-index", "--no-fsmonitor")
                    unexpected.unlink(missing_ok=True)
                    target.write_bytes(original)
                    self.git("restore", "--staged", "--", path)
                self.assertEqual(self.git("ls-files", "--stage", "-z"), index)
                self.assertEqual(target.read_bytes(), original)
                self.assert_both_paths(True)
