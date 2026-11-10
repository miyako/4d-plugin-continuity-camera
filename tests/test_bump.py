"""Real bare-Git advertisement races and branch-only CLI regressions."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from helper_support import GitFixture, cwd, source
import bump


class BumpTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.hook = self.local / ".github/hooks/pre-push"
        self.original_run = bump.run

    def call(self):
        with cwd(self.local), patch.dict(os.environ, self.env, clear=True), \
                patch.object(bump, "HOOK", self.hook):
            return bump.bump_branch(self.sha, self.ref, "patch")

    def assert_unchanged(self):
        self.assertEqual(self.remote(), self.sha)
        self.assertEqual(self.git("--git-dir=" + str(self.bare), "tag", "--list"), "")

    def test_real_detached_cli_without_identity_clean_direct_child_source_only_and_no_follow_tags(self):
        self.git("tag", "-a", "unrelated-annotated", "-m", "must not follow")
        self.git("checkout", "--quiet", "--detach", self.sha)
        self.git("config", "--unset-all", "user.name")
        self.git("config", "--unset-all", "user.email")
        result = self.cli(".github/scripts/bump.py", "patch", "--sha", self.sha, "--ref", self.ref)
        self.assertEqual(result.returncode, 0, result.stderr)
        new = self.git("rev-parse", "HEAD")
        self.assertNotEqual(new, self.sha)
        self.assertEqual(self.remote(), new)
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", new), f"{new} {self.sha}")
        changed = set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", new).splitlines())
        self.assertEqual(changed, {str(source.PROJECT), str(source.LOCALIZED)})
        self.assertEqual((self.local / source.PLIST).read_bytes(),
                         subprocess.check_output(["git", "show", self.sha + ":" + str(source.PLIST)],
                                                 cwd=self.local, env=self.env))
        message = self.git("log", "-1", "--format=%B")
        self.assertTrue(message.endswith(
            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
        ), message)
        self.assertEqual(self.git("status", "--porcelain"), "")
        identity = "github-actions[bot] <github-actions[bot]@users.noreply.github.com>"
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>%n%cn <%ce>"),
                         identity + "\n" + identity)
        self.assertEqual(self.git("config", "push.followTags"), "true")
        self.assertEqual(self.git("--git-dir=" + str(self.bare), "tag", "--list"), "")
        self.assertEqual(source.read(self.local), source.read().rsplit(".", 1)[0] + "." +
                         str(int(source.read().split(".")[-1]) + 1))

    def test_no_follow_tags_negative_control_really_follows_annotated_tag(self):
        self.git("tag", "-a", "negative-control", "-m", "local fixture only")
        self.git("push", "--quiet", "origin", f"HEAD:{self.ref}")
        self.assertEqual(self.git("--git-dir=" + str(self.bare), "tag", "--list"), "negative-control")

    def test_initial_sha_ref_dirty_hidden_flag_and_guard_errors_are_zero_write(self):
        before = {path: (self.local / path).read_bytes() for path in source.SOURCE_PATHS}
        for ref in ("selected", "refs/tags/v1.0.0", "refs/heads/a..b"):
            result = self.cli(".github/scripts/bump.py", "patch", "--sha", self.sha, "--ref", ref)
            self.assertNotEqual(result.returncode, 0)
        for sha in ("a" * 39, "A" * 40, self.ancestor):
            result = self.cli(".github/scripts/bump.py", "patch", "--sha", sha, "--ref", self.ref)
            self.assertNotEqual(result.returncode, 0)
        for flag in ("--assume-unchanged", "--skip-worktree"):
            path = str(source.PLIST)
            self.git("update-index", flag, "--", path)
            (self.local / path).write_bytes(before[source.PLIST] + b" ")
            result = self.cli(".github/scripts/bump.py", "patch", "--sha", self.sha, "--ref", self.ref)
            self.assertNotEqual(result.returncode, 0)
            (self.local / path).write_bytes(before[source.PLIST])
            self.git("update-index", "--no-" + flag[2:], "--", path)
        for missing in (False, True):
            data = self.hook.read_bytes()
            if missing:
                self.hook.unlink()
            else:
                self.hook.chmod(0o644)
            result = self.cli(".github/scripts/bump.py", "patch", "--sha", self.sha, "--ref", self.ref)
            self.assertNotEqual(result.returncode, 0)
            self.hook.write_bytes(data)
            self.hook.chmod(0o755)
        self.assertEqual(before, {path: (self.local / path).read_bytes() for path in before})
        self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)
        self.assert_unchanged()

    def test_initial_advance_rewind_and_delete(self):
        self.git("commit", "--quiet", "--allow-empty", "-m", "concurrent advance")
        advance = self.git("rev-parse", "HEAD")
        self.git("--git-dir=" + str(self.bare), "fetch", "--quiet", "--no-tags",
                 str(self.local), advance)
        self.git("reset", "--hard", self.sha)
        for new in (advance, self.ancestor, "0" * 40):
            self.git("--git-dir=" + str(self.bare), "update-ref", self.ref, new)
            with self.assertRaises(ValueError):
                self.call()
            self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)
            self.git("--git-dir=" + str(self.bare), "update-ref", self.ref, self.sha)
        self.assert_unchanged()

    def race(self, kind):
        self.git("tag", "-a", "race-annotated", "-m", "must not follow")
        self.git("commit", "--quiet", "--allow-empty", "-m", "peer advance")
        advance = self.git("rev-parse", "HEAD")
        self.git("--git-dir=" + str(self.bare), "fetch", "--quiet", "--no-tags",
                 str(self.local), advance)
        self.git("reset", "--hard", self.sha)
        self.git("checkout", "--quiet", "--detach", self.sha)
        expected, pushes = None, 0

        def intercept(*args, **kwargs):
            nonlocal expected, pushes
            if "push" in args:
                pushes += 1
                new = self.git("rev-parse", "HEAD")
                if kind == "same-result":
                    self.git("--git-dir=" + str(self.bare), "fetch", "--quiet", "--no-tags",
                             str(self.local), new)
                    expected = new
                else:
                    expected = {"advance": advance, "rewind": self.ancestor, "delete": "0" * 40}[kind]
                self.git("--git-dir=" + str(self.bare), "update-ref", self.ref, expected)
            return self.original_run(*args, **kwargs)

        with patch.object(bump, "run", side_effect=intercept):
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                self.call()
        self.assertEqual(pushes, 1)
        if kind == "delete":
            self.assertNotEqual(self.git("--git-dir=" + str(self.bare), "rev-parse",
                                         "--verify", self.ref, check=False).returncode, 0)
        else:
            self.assertEqual(self.remote(), expected)
        self.assertEqual(self.git("--git-dir=" + str(self.bare), "tag", "--list"), "")

    def test_advertised_advance_after_final_read(self):
        self.race("advance")

    def test_advertised_rewind_after_final_read(self):
        self.race("rewind")

    def test_advertised_delete_after_final_read(self):
        self.race("delete")

    def test_advertised_same_result_zero_updates_after_final_read(self):
        self.race("same-result")

    def test_postadvertisement_peer_update_cannot_be_overwritten(self):
        self.git("commit", "--quiet", "--allow-empty", "-m", "peer after advertisement")
        peer = self.git("rev-parse", "HEAD")
        self.git("--git-dir=" + str(self.bare), "fetch", "--quiet", "--no-tags",
                 str(self.local), peer)
        self.git("reset", "--hard", self.sha)
        receive = self.bare / "hooks/pre-receive"
        receive.parent.mkdir(exist_ok=True)
        receive.write_text(
            "#!/bin/sh\nset -eu\nunset GIT_QUARANTINE_PATH\n"
            f'git --git-dir="{self.bare}" update-ref "{self.ref}" "{peer}" "{self.sha}"\n'
        )
        receive.chmod(0o755)
        result = self.cli(".github/scripts/bump.py", "patch", "--sha", self.sha, "--ref", self.ref)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote(), peer)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), peer)
        self.assertEqual(self.git("--git-dir=" + str(self.bare), "tag", "--list"), "")

    def test_late_missing_and_nonexecutable_guard(self):
        runtime = self.root / "runtime"
        shutil.copytree(self.local / ".github", runtime)
        hook = runtime / "hooks/pre-push"
        reads = 0
        real_remote = bump.remote_ref

        def lose(ref):
            nonlocal reads
            value = real_remote(ref)
            reads += 1
            if reads == 2:
                hook.chmod(0o644)
            return value

        with patch.object(bump, "remote_ref", side_effect=lose), \
                cwd(self.local), patch.dict(os.environ, self.env, clear=True), \
                patch.object(bump, "HOOK", hook):
            with self.assertRaisesRegex(ValueError, "executable"):
                bump.bump_branch(self.sha, self.ref, "patch")
        self.assertEqual(reads, 2)
        self.assert_unchanged()
        self.git("reset", "--hard", self.sha)
        hook.chmod(0o755)
        reads = 0

        def remove(ref):
            nonlocal reads
            value = real_remote(ref)
            reads += 1
            if reads == 2:
                hook.unlink()
            return value

        with patch.object(bump, "remote_ref", side_effect=remove), \
                cwd(self.local), patch.dict(os.environ, self.env, clear=True), \
                patch.object(bump, "HOOK", hook):
            with self.assertRaisesRegex(ValueError, "executable"):
                bump.bump_branch(self.sha, self.ref, "patch")
        self.assert_unchanged()

    def guarded_cli_with_git_log(self, tamper=None):
        tools = self.root / "guard-tools"
        tools.mkdir(exist_ok=True)
        log = self.root / "guard-git.jsonl"
        log.unlink(missing_ok=True)
        counter = self.root / "guard-reads"
        counter.unlink(missing_ok=True)
        fake = self.root / "exit-zero-hook"
        fake.write_bytes(self.hook.read_bytes())
        fake.chmod(0o755)
        wrapper = tools / "git"
        wrapper.write_text(f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
with open(os.environ["GUARD_GIT_LOG"], "a") as out:
    out.write(json.dumps(args) + "\\n")
result = subprocess.run([{shutil.which("git")!r}, *args], capture_output=True)
if args[:3] == ["ls-remote", "--refs", "origin"] and result.returncode == 0:
    counter = pathlib.Path(os.environ["GUARD_READS"])
    reads = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(reads))
    if reads == 2 and os.environ.get("GUARD_TAMPER"):
        hook = pathlib.Path(".github/hooks/pre-push")
        if os.environ["GUARD_TAMPER"] == "symlink":
            hook.unlink()
            hook.symlink_to(os.environ["GUARD_FAKE"])
        else:
            hook.write_bytes(b"#!/bin/sh\\nexit 0\\n")
            hook.chmod(0o755)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
""")
        wrapper.chmod(0o755)
        upload_pack = shutil.which("git-upload-pack") or (
            Path(subprocess.check_output(["git", "--exec-path"], text=True).strip())
            / "git-upload-pack")
        for name, executable in (("python3", sys.executable),
                                 ("git-upload-pack", upload_pack)):
            target = tools / name
            if not target.exists():
                target.symlink_to(executable)
        env = {**self.env, "PATH": str(tools), "GUARD_GIT_LOG": str(log),
               "GUARD_READS": str(counter), "GUARD_FAKE": str(fake),
               "GUARD_TAMPER": tamper or ""}
        result = self.cli(".github/scripts/bump.py", "patch",
                          "--sha", self.sha, "--ref", self.ref, env=env)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("guard", result.stderr)
        self.assertFalse(any("push" in args for args in calls), calls)
        self.assert_unchanged()
        return counter

    def test_initial_symlink_and_exit_zero_executable_guard_fail_without_writes_or_push(self):
        before = {path: (self.local / path).read_bytes() for path in source.SOURCE_PATHS}
        original = self.hook.read_bytes()
        target = self.root / "original-executable-guard"
        target.write_bytes(original)
        target.chmod(0o755)
        for kind in ("symlink", "replacement"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.hook.unlink()
                    self.hook.symlink_to(target)
                else:
                    self.hook.write_bytes(b"#!/bin/sh\nexit 0\n")
                    self.hook.chmod(0o755)
                counter = self.guarded_cli_with_git_log()
                self.assertFalse(counter.exists(), "Initial rejection must precede remote reads")
                self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)
                self.assertEqual(before, {path: (self.local / path).read_bytes() for path in before})
                self.hook.unlink()
                self.hook.write_bytes(original)
                self.hook.chmod(0o755)

    def test_executable_content_or_symlink_swap_after_final_real_remote_read_never_pushes(self):
        for kind in ("replacement", "symlink"):
            with self.subTest(kind=kind):
                self.git("checkout", "--quiet", "--detach", self.sha)
                counter = self.guarded_cli_with_git_log(kind)
                self.assertEqual(counter.read_text(), "2")
                child = self.git("rev-parse", "HEAD")
                self.assertNotEqual(child, self.sha)
                self.assertEqual(self.git("rev-list", "--parents", "-n", "1", child),
                                 f"{child} {self.sha}")
                self.git("reset", "--hard", self.sha)

    def test_guard_requires_head_entry_to_be_a_committed_executable_blob(self):
        self.git("update-index", "--chmod=-x", "--", ".github/hooks/pre-push")
        self.git("commit", "--quiet", "-m", "nonexecutable committed guard fixture")
        self.hook.chmod(0o755)
        with cwd(self.local), patch.object(bump, "HOOK", self.hook):
            with self.assertRaisesRegex(ValueError, "committed executable blob"):
                bump.require_guard()
        self.git("update-index", "--force-remove", "--", ".github/hooks/pre-push")
        self.git("commit", "--quiet", "-m", "untracked executable guard fixture")
        with cwd(self.local), patch.object(bump, "HOOK", self.hook):
            with self.assertRaisesRegex(ValueError, "committed executable blob"):
                bump.require_guard()
        self.assert_unchanged()

    def test_actual_cli_committed_missing_and_nonexecutable_guard_are_aligned_zero_write_zero_remote(self):
        selected = self.sha
        hook_bytes = self.hook.read_bytes()
        for kind in ("nonexecutable", "missing"):
            with self.subTest(kind=kind):
                self.git("reset", "--hard", selected)
                if kind == "nonexecutable":
                    self.git("update-index", "--chmod=-x", "--", ".github/hooks/pre-push")
                else:
                    self.git("update-index", "--force-remove", "--", ".github/hooks/pre-push")
                self.git("commit", "--quiet", "-m", "prospective committed " + kind + " guard")
                self.sha = self.git("rev-parse", "HEAD")
                self.git("--git-dir=" + str(self.bare), "fetch", "--quiet", "--no-tags",
                         str(self.local), self.sha)
                self.git("--git-dir=" + str(self.bare), "update-ref", self.ref, self.sha)
                self.hook.write_bytes(hook_bytes)
                self.hook.chmod(0o755)
                self.assertEqual(self.remote(), self.sha)
                self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)
                before = {path: (self.local / path).read_bytes() for path in source.SOURCE_PATHS}
                indexed = self.git("ls-files", "--stage", "-z")
                counter = self.guarded_cli_with_git_log()
                self.assertFalse(counter.exists(), "Initial guard failure must precede remote reads")
                self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)
                self.assertEqual(self.git("ls-files", "--stage", "-z"), indexed)
                self.assertEqual(before, {path: (self.local / path).read_bytes() for path in before})

    def prepare_child(self):
        result = self.cli(".github/scripts/source_version.py", "--bump", "patch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git("add", "--", *map(str, source.SOURCE_PATHS))
        self.git("config", "user.name", "github-actions[bot]")
        self.git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
        self.git("commit", "--quiet", "-m", "source child", "-m", bump.TRAILER)
        return self.git("rev-parse", "HEAD")

    def invoke_guard(self, new, rows, env=None):
        values = {**self.env, "BUMP_EXPECTED_SHA": self.sha,
                  "BUMP_EXPECTED_REF": self.ref, "BUMP_NEW_SHA": new}
        return subprocess.run([str(self.hook)], cwd=self.local, input=rows,
                              env=values if env is None else env, text=True, capture_output=True)

    def test_guard_exact_one_update_literal_head_ref_oldsha_and_missing_environment(self):
        new = self.prepare_child()
        row = f"{new} {new} {self.ref} {self.sha}\n"
        self.assertEqual(self.invoke_guard(new, row).returncode, 0)
        self.assertEqual(self.invoke_guard(new, row.replace(new + " ", "HEAD ", 1)).returncode, 0)
        for invalid in ("", row * 2, "malformed\n", row.replace(self.sha, self.ancestor),
                        row.replace(self.ref, "refs/heads/other"),
                        row.replace(new + " ", "refs/heads/selected ", 1),
                        row.replace(new, self.sha, 1)):
            self.assertNotEqual(self.invoke_guard(new, invalid).returncode, 0)
        self.assertNotEqual(self.invoke_guard(new, row, self.env).returncode, 0)
        self.assert_unchanged()

    def test_guard_revalidates_source_only_nonempty_trailer_and_direct_parent(self):
        new = self.prepare_child()
        tree = self.git("rev-parse", "HEAD^{tree}")
        selected_tree = self.git("rev-parse", self.sha + "^{tree}")
        for parents, selected, message in (
            (["-p", self.sha, "-p", self.ancestor], tree, "merge\n\n" + bump.TRAILER),
            (["-p", self.sha], selected_tree, "empty\n\n" + bump.TRAILER),
            (["-p", new], tree, "grandchild\n\n" + bump.TRAILER),
        ):
            commit = self.git("commit-tree", selected, *parents, input=message)
            self.git("reset", "--hard", commit)
            result = self.invoke_guard(commit, f"{commit} {commit} {self.ref} {self.sha}\n")
            self.assertNotEqual(result.returncode, 0, result.stderr)
        self.git("reset", "--hard", new)
        real_run = bump.run

        def missing_trailer(*args, **kwargs):
            if args[:4] == ("git", "log", "-1", "--format=%B"):
                return "simulated missing trailer"
            return real_run(*args, **kwargs)

        with cwd(self.local), patch.object(bump, "HOOK", self.hook), \
                patch.object(bump, "run", side_effect=missing_trailer):
            with self.assertRaisesRegex(ValueError, "trailer"):
                bump.commit_ready(self.sha, self.ref, new)
        (self.local / "unrelated.cpp").write_text("extra source")
        self.git("add", ".")
        tree = self.git("write-tree")
        commit = self.git("commit-tree", tree, "-p", self.sha,
                          input="extra\n\n" + bump.TRAILER + "\n")
        self.git("reset", "--hard", commit)
        self.assertNotEqual(self.invoke_guard(
            commit, f"{commit} {commit} {self.ref} {self.sha}\n").returncode, 0)
        self.assert_unchanged()

    def test_postpush_exact_remote_sha_is_verified(self):
        reads = 0
        real_remote = bump.remote_ref

        def response(ref):
            nonlocal reads
            reads += 1
            actual = real_remote(ref)
            return self.sha if reads == 3 else actual

        with patch.object(bump, "remote_ref", side_effect=response):
            with self.assertRaisesRegex(ValueError, "exact-SHA"):
                self.call()
        self.assertEqual(reads, 3)
        self.assertEqual(self.remote(), self.git("rev-parse", "HEAD"))

    def test_actual_cli_remote_races_after_second_actual_ls_remote_before_advertisement(self):
        self.git("commit", "--quiet", "--allow-empty", "-m", "remote peer fixture")
        advance = self.git("rev-parse", "HEAD")
        self.git("--git-dir=" + str(self.bare), "fetch", "--quiet", "--no-tags",
                 str(self.local), advance)
        self.git("reset", "--hard", self.sha)
        tools = self.root / "race-tools"
        tools.mkdir()
        real_git = shutil.which("git")
        wrapper = tools / "git"
        wrapper.write_text(f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
with open(os.environ["RACE_LOG"], "a") as out:
    out.write(json.dumps(args)+"\\n")
result = subprocess.run([{real_git!r}, *args], capture_output=True)
if args[:3] == ["ls-remote", "--refs", "origin"] and result.returncode == 0:
    counter = pathlib.Path(os.environ["RACE_READS"])
    reads = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(reads))
    if reads == 2:
        kind = os.environ["RACE_KIND"]
        bare = "--git-dir=" + os.environ["RACE_BARE"]
        ref = os.environ["RACE_REF"]
        if kind == "same-result":
            new = subprocess.check_output([{real_git!r}, "rev-parse", "HEAD"], text=True).strip()
            subprocess.run([{real_git!r}, bare, "fetch", "--quiet", "--no-tags",
                            str(pathlib.Path.cwd()), new], check=True)
        else:
            new = {{"advance":os.environ["RACE_ADVANCE"], "rewind":os.environ["RACE_ANCESTOR"],
                    "delete":"0"*40}}[kind]
        subprocess.run([{real_git!r}, bare, "update-ref", ref, new], check=True)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
""")
        wrapper.chmod(0o755)
        (tools / "python3").symlink_to(sys.executable)
        upload_pack = Path(subprocess.check_output(["git", "--exec-path"], text=True).strip()) / "git-upload-pack"
        (tools / "git-upload-pack").symlink_to(upload_pack)
        for kind in ("advance", "rewind", "delete", "same-result"):
            with self.subTest(kind=kind):
                self.git("checkout", "--quiet", "--detach", self.sha)
                self.git("--git-dir=" + str(self.bare), "update-ref", self.ref, self.sha)
                log, reads = self.root / "race.jsonl", self.root / "reads"
                log.unlink(missing_ok=True)
                reads.unlink(missing_ok=True)
                result = self.cli(".github/scripts/bump.py", "patch", "--sha", self.sha,
                                  "--ref", self.ref, env={
                                      **self.env, "PATH": str(tools), "RACE_LOG": str(log),
                                      "RACE_READS": str(reads), "RACE_KIND": kind,
                                      "RACE_BARE": str(self.bare), "RACE_REF": self.ref,
                                      "RACE_ADVANCE": advance, "RACE_ANCESTOR": self.ancestor})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(reads.read_text(), "2")
                calls = [json.loads(row) for row in log.read_text().splitlines()]
                self.assertEqual(sum("push" in args for args in calls), 1)
                child = self.git("rev-parse", "HEAD")
                self.assertNotEqual(child, self.sha)
                self.assertEqual(self.git("rev-list", "--parents", "-n", "1", child),
                                 child + " " + self.sha)
                if kind == "delete":
                    self.assertNotEqual(self.git("--git-dir=" + str(self.bare), "rev-parse",
                                                 "--verify", self.ref, check=False).returncode, 0)
                else:
                    self.assertEqual(self.remote(), {
                        "advance": advance, "rewind": self.ancestor, "same-result": child}[kind])
                self.assertEqual(self.git("--git-dir=" + str(self.bare), "tag", "--list"), "")

    def test_genuine_missing_wrong_or_nonending_trailer_commits_fail_guard(self):
        child = self.prepare_child()
        tree = self.git("rev-parse", child + "^{tree}")
        bot = "github-actions[bot]"
        env = {**self.env, "GIT_AUTHOR_NAME": bot, "GIT_COMMITTER_NAME": bot,
               "GIT_AUTHOR_EMAIL": bot + "@users.noreply.github.com",
               "GIT_COMMITTER_EMAIL": bot + "@users.noreply.github.com"}
        for message in ("missing trailer", "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
                        bump.TRAILER + "\n\nnot ending"):
            commit = subprocess.check_output(
                ["git", "commit-tree", tree, "-p", self.sha], input=message,
                cwd=self.local, env=env, text=True).strip()
            self.git("reset", "--hard", commit)
            self.assertNotEqual(self.invoke_guard(
                commit, f"{commit} {commit} {self.ref} {self.sha}\n").returncode, 0)

    def test_literal_last_trailer_line_actual_hook_and_bump_guard_cli_each_restored_positive(self):
        good = self.prepare_child()
        tree = self.git("rev-parse", good + "^{tree}")
        bot = "github-actions[bot]"
        identity = {
            **self.env, "GIT_AUTHOR_NAME": bot, "GIT_COMMITTER_NAME": bot,
            "GIT_AUTHOR_EMAIL": bot + "@users.noreply.github.com",
            "GIT_COMMITTER_EMAIL": bot + "@users.noreply.github.com",
        }
        tools = self.root / "literal-trailer-tools"
        tools.mkdir()
        log = self.root / "literal-trailer-git.jsonl"
        wrapper = tools / "git"
        wrapper.write_text(f"""#!{sys.executable}
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["LITERAL_GIT_LOG"], "a") as out:
    out.write(json.dumps(args) + "\\n")
if any(value in args for value in ("push", "commit", "commit-tree", "update-ref", "add")):
    sys.exit("Guard must not mutate or push")
result = subprocess.run([{shutil.which("git")!r}, *args], capture_output=True)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
""")
        wrapper.chmod(0o755)
        for name, executable in (("python3", sys.executable), ("dirname", shutil.which("dirname"))):
            (tools / name).symlink_to(executable)

        def snapshot():
            return {
                "head": self.git("rev-parse", "HEAD"),
                "index": self.git("ls-files", "--stage", "-z"),
                "index_bytes": (self.local / ".git/index").read_bytes(),
                "v": self.git("ls-files", "-v"), "f": self.git("ls-files", "-f"),
                "raw": {
                    name: ((self.local / name).read_bytes(), (self.local / name).stat().st_mode & 0o777)
                    for name in self.git("ls-files", "-z").split("\0") if name
                },
                "remote": self.remote(),
                "remote_tags": self.git("--git-dir=" + str(self.bare), "tag", "--list"),
            }

        def invoke(commit, entrypoint):
            log.unlink(missing_ok=True)
            env = {
                **self.env, "PATH": str(tools), "LITERAL_GIT_LOG": str(log),
                "BUMP_EXPECTED_SHA": self.sha, "BUMP_EXPECTED_REF": self.ref,
                "BUMP_NEW_SHA": commit,
            }
            command = ([str(self.hook)] if entrypoint == "executable-hook" else
                       [sys.executable, ".github/scripts/bump.py", "--guard"])
            before = snapshot()
            result = subprocess.run(
                command, cwd=self.local, env=env, text=True, capture_output=True,
                input=f"{commit} {commit} {self.ref} {self.sha}\n", timeout=60)
            self.assertEqual(snapshot(), before, result.stderr)
            calls = [json.loads(row) for row in log.read_text().splitlines()]
            self.assertTrue(any(args[:4] == ["log", "-1", "--format=%B", commit] for args in calls))
            self.assertFalse(any("push" in args or "ls-remote" in args for args in calls), calls)
            self.assertFalse(any(value in args for args in calls
                                 for value in ("commit", "commit-tree", "update-ref", "add")), calls)
            return result

        for kind, message in (
            ("prefix", "not a trailer: " + bump.TRAILER),
            ("leading-space", " " + bump.TRAILER),
            ("trailing-space", bump.TRAILER + " "),
            ("trailing-tab", bump.TRAILER + "\t"),
            ("nonending", bump.TRAILER + "\n\nlater text"),
        ):
            bad = subprocess.check_output(
                ["git", "commit-tree", tree, "-p", self.sha], input=message + "\n",
                cwd=self.local, env=identity, text=True).strip()
            self.assertEqual(self.git("log", "-1", "--format=%B", bad, check=False)
                             .stdout.rstrip("\n"), message)
            for entrypoint in ("executable-hook", "bump-guard-cli"):
                with self.subTest(kind=kind, entrypoint=entrypoint):
                    self.git("reset", "--hard", bad)
                    try:
                        result = invoke(bad, entrypoint)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                    finally:
                        self.git("reset", "--hard", good)
                        result = invoke(good, entrypoint)
                        self.assertEqual(result.returncode, 0, result.stderr)
