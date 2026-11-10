"""One guarded source-only branch update; never tag, publish, build or dispatch."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
import repository
import source_version


TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
HOOK = Path(__file__).resolve().parents[1] / "hooks/pre-push"
run = repository.run


def require_guard():
    if HOOK.is_symlink() or not HOOK.is_file() or not os.access(HOOK, os.X_OK):
        raise ValueError("Mandatory executable advertised-SHA guard is missing")
    relative = ".github/hooks/pre-push"
    committed = repository.entries(run("git", "ls-tree", "-z", "HEAD", "--", relative))
    expected = committed.get(relative)
    if expected is None or expected[0] != "100755" or repository.actual_blob(HOOK) != expected:
        raise ValueError("Mandatory guard must match its committed executable blob")


def remote_ref(ref):
    lines = run("git", "ls-remote", "--refs", "origin", ref).splitlines()
    if len(lines) != 1:
        raise ValueError("Selected remote branch is missing or ambiguous")
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != ref or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
        raise ValueError("Malformed selected remote branch advertisement")
    return fields[0]


def commit_ready(sha, ref, new):
    require_guard()
    if not ref.startswith("refs/heads/"):
        raise ValueError("Bump requires a full selected branch ref")
    run("git", "check-ref-format", ref)
    repository.verify_checkout(new)
    if new == sha or run("git", "rev-list", "--parents", "-n", "1", new) != f"{new} {sha}":
        raise ValueError("Bump commit must have selected SHA as its single direct parent")
    paths = set(filter(None, run("git", "diff-tree", "--no-commit-id", "--name-only",
                                "--no-renames", "-r", "-z", new).split("\0")))
    if not paths or not paths.issubset(set(map(str, source_version.SOURCE_PATHS))):
        raise ValueError("Bump commit must be nonempty and source-version-only")
    message = run("git", "log", "-1", "--format=%B", new)
    if message.rsplit("\n", 1)[-1] != TRAILER:
        raise ValueError("Bump commit requires the literal ending co-author trailer")
    identity = "github-actions[bot] <github-actions[bot]@users.noreply.github.com>"
    if run("git", "log", "-1", "--format=%an <%ae>%n%cn <%ce>", new) != identity + "\n" + identity:
        raise ValueError("Bump commit requires explicit Actions bot author and committer")
    source_version.read()


def guard(updates):
    sha, ref, new = (os.environ[key] for key in
                     ("BUMP_EXPECTED_SHA", "BUMP_EXPECTED_REF", "BUMP_NEW_SHA"))
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (sha, new)):
        raise ValueError("Invalid guarded SHA")
    commit_ready(sha, ref, new)
    rows = updates.splitlines()
    if len(rows) != 1 or len(rows[0].split()) != 4:
        raise ValueError("Expected exactly one advertised branch update")
    local_ref, local_sha, remote, old = rows[0].split()
    if local_ref not in (new, "HEAD") or (local_sha, remote, old) != (new, ref, sha):
        raise ValueError("Advertised old SHA/ref or literal new SHA/HEAD differs")


def bump_branch(sha, ref, mode):
    require_guard()
    if not ref.startswith("refs/heads/"):
        raise ValueError("Bump requires a full selected branch ref")
    run("git", "check-ref-format", ref)
    repository.verify_checkout(sha)
    if remote_ref(ref) != sha:
        raise ValueError("Selected branch moved since dispatch")
    version = source_version.bump(mode)
    run("git", "add", "--", *map(str, source_version.SOURCE_PATHS))
    run("git", "-c", "user.name=github-actions[bot]", "-c",
        "user.email=github-actions[bot]@users.noreply.github.com",
        "commit", "-m", f"chore: bump version to {version}", "-m", TRAILER,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "github-actions[bot]",
             "GIT_AUTHOR_EMAIL": "github-actions[bot]@users.noreply.github.com",
             "GIT_COMMITTER_NAME": "github-actions[bot]",
             "GIT_COMMITTER_EMAIL": "github-actions[bot]@users.noreply.github.com"})
    new = run("git", "rev-parse", "HEAD")
    commit_ready(sha, ref, new)
    if remote_ref(ref) != sha:
        raise ValueError("Selected branch moved during bump")
    require_guard()
    result = run("git", "-c", f"core.hooksPath={HOOK.parent}",
                 "-c", "push.followTags=false", "push", "--porcelain", "--no-follow-tags",
                 "origin", f"{new}:{ref}", env={
                     **os.environ, "BUMP_EXPECTED_SHA": sha,
                     "BUMP_EXPECTED_REF": ref, "BUMP_NEW_SHA": new,
                 })
    if not any(line.startswith(" \t") and line.split("\t")[1] == f"{new}:{ref}"
               for line in result.splitlines()):
        raise ValueError("Branch push was not a new update")
    if remote_ref(ref) != new:
        raise ValueError("Pushed branch failed exact-SHA verification")
    print(f"Bumped source to {version}: {new}; run Build separately on {ref}")
    return new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("patch", "minor", "major"))
    parser.add_argument("--sha")
    parser.add_argument("--ref")
    parser.add_argument("--guard", action="store_true")
    args = parser.parse_args(argv)
    if args.guard:
        guard(sys.stdin.read())
    elif args.mode and args.sha and args.ref:
        bump_branch(args.sha, args.ref, args.mode)
    else:
        parser.error("mode, --sha and --ref are required")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Bump stopped: {error}. No rollback/retry; inspect retained state.", file=sys.stderr)
        sys.exit(1)
