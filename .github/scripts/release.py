"""Authenticated create-only publication; retain partial state for manual recovery."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

sys.dont_write_bytecode = True
import native
import repository
import source_version


REPOSITORY = "miyako/4d-plugin-continuity-camera"
PRODUCT = "Continuity Camera"
TAG = re.compile("v" + source_version.VERSION + r"\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


class ReleaseError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def numeric_id(value):
    return type(value) is int and value > 0


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def finite_float(value):
    result = float(value)
    require(math.isfinite(result), f"Invalid nonfinite JSON number: {value}")
    return result


class GitHub:
    def api(self, method, endpoint, payload=None, asset=None):
        command = ["gh", "api", "--method", method, endpoint,
                   "-H", "Accept: application/vnd.github+json",
                   "-H", "X-GitHub-Api-Version: 2022-11-28"]
        data = None
        if asset is not None:
            command += ["-H", "Content-Type: application/octet-stream", "--input", str(asset)]
        elif payload is not None:
            command += ["--input", "-"]
            data = json.dumps(payload)
        try:
            result = subprocess.run(command, input=data, text=True, capture_output=True, check=False)
            require(result.returncode == 0,
                    f"{method} {endpoint}: gh failed: {result.stderr.strip()}")
            return json.loads(result.stdout, object_pairs_hook=json_object,
                              parse_constant=invalid_constant, parse_float=finite_float)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ReleaseError(f"{method} {endpoint}: transport/JSON failure: {error}") from error


def validate_ref(entry):
    require(isinstance(entry, dict), "Malformed tag ref entry")
    ref, obj = entry.get("ref"), entry.get("object")
    require(isinstance(ref, str) and ref.startswith("refs/tags/") and len(ref) > 10,
            "Malformed tag ref name")
    require(isinstance(obj, dict) and isinstance(obj.get("sha"), str)
            and SHA.fullmatch(obj["sha"]) and obj.get("type") in ("commit", "tag"),
            "Malformed tag ref object")
    return ref, obj["sha"], obj["type"]


def remote_tag(tag):
    ref = "refs/tags/" + tag
    result = repository.run("git", "ls-remote", "--refs", "origin", ref)
    if not result:
        return None
    rows = result.splitlines()
    require(len(rows) == 1, "Ambiguous remote tag advertisement")
    fields = rows[0].split()
    require(len(fields) == 2 and fields[1] == ref and SHA.fullmatch(fields[0]),
            "Malformed exact remote tag advertisement")
    return fields[0]


def available(api, tag):
    require(isinstance(tag, str) and TAG.fullmatch(tag), "Expected canonical vX.Y.Z tag")
    release_ids, occupied_release, page = set(), False, 1
    while True:
        releases = api.api("GET", f"repos/{REPOSITORY}/releases?per_page=100&page={page}")
        require(isinstance(releases, list) and len(releases) <= 100, "Malformed releases page")
        for entry in releases:
            require(isinstance(entry, dict) and numeric_id(entry.get("id"))
                    and isinstance(entry.get("tag_name"), str) and bool(entry["tag_name"])
                    and type(entry.get("draft")) is bool
                    and type(entry.get("prerelease")) is bool, "Malformed release entry")
            require(entry["id"] not in release_ids, "Duplicate release ID across release pages")
            release_ids.add(entry["id"])
            occupied_release |= entry["tag_name"] == tag
        if not releases:
            break
        page += 1
    refs = api.api("GET", f"repos/{REPOSITORY}/git/matching-refs/tags/{quote(tag, safe='')}")
    require(isinstance(refs, list), "Malformed matching tag refs")
    names = set()
    for entry in refs:
        name, _, _ = validate_ref(entry)
        require(name.startswith("refs/tags/" + tag), "Unexpected matching tag prefix")
        require(name not in names, "Duplicate matching tag ref")
        names.add(name)
    require(not occupied_release and "refs/tags/" + tag not in names,
            f"{tag} already has a tag or draft/published release")
    require(remote_tag(tag) is None, f"{tag} already exists in the remote advertisement")
    return release_ids


def asset_names(version):
    return [f"{PRODUCT}-{version}.zip", f"{PRODUCT}-{version}.dmg"]


def authenticated_api():
    api = GitHub()
    repo = api.api("GET", "repos/" + REPOSITORY)
    require(isinstance(repo, dict) and numeric_id(repo.get("id"))
            and repo.get("full_name") == REPOSITORY,
            "Token must authenticate the exact canonical repository")
    return api


def context(sha, *, final=False):
    require(bool(os.environ.get("GH_TOKEN", "").strip()),
            "Explicit nonempty GH_TOKEN is required")
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Unexpected publication repository")
    require(os.environ.get("GITHUB_SHA") == sha, "--sha must match immutable GITHUB_SHA")
    repository.selected_sha(sha)
    version = source_version.read()
    if final:
        native.validate_paths((*native.FILES, *native.DIRECTORIES, native.XCODE_DATA,
                               *map(Path, asset_names(version))))
    repository.verify_checkout(sha, generated=native.generated,
                               assets=asset_names(version), final=final)
    if final:
        source_version.verify_bundle(native.BUNDLE)
        native.validate_final()
    return version, "v" + version


def preflight(sha):
    version, tag = context(sha)
    available(authenticated_api(), tag)
    return version, tag


def check(sha):
    version, tag = context(sha, final=True)
    available(authenticated_api(), tag)
    return version, tag


def validate_assets(version, assets):
    require([str(path) for path in assets] == asset_names(version),
            "Assets must be the exact ordered unqualified selected-version ZIP and DMG paths")
    for path in map(Path, assets):
        require(not path.is_symlink() and path.is_file() and path.stat().st_size > 0,
                f"Asset must be a nonempty regular nonsymlink file: {path}")
        with path.open("rb"):
            pass


def publish(sha, assets):
    version, tag = context(sha, final=True)
    validate_assets(version, assets)
    assets = list(map(Path, assets))
    api = authenticated_api()
    existing_ids = available(api, tag)
    tag_attempted, tag_created, release_attempted = False, False, False
    release_id, upload_attempted, uploaded = None, None, []
    try:
        tag_attempted = True
        created = api.api("POST", f"repos/{REPOSITORY}/git/refs",
                         {"ref": f"refs/tags/{tag}", "sha": sha})
        require(validate_ref(created) == ("refs/tags/" + tag, sha, "commit"),
                "Created tag does not match selected SHA")
        tag_created = True
        remote = api.api("GET", f"repos/{REPOSITORY}/git/ref/tags/{tag}")
        require(validate_ref(remote) == ("refs/tags/" + tag, sha, "commit"),
                "Remote tag failed exact-SHA verification")
        require(remote_tag(tag) == sha, "Remote advertisement failed exact tag/SHA verification")
        release_attempted = True
        created = api.api("POST", f"repos/{REPOSITORY}/releases", {
            "tag_name": tag, "name": tag, "target_commitish": sha,
            "draft": False, "prerelease": False, "body": "", "generate_release_notes": False,
        })
        if isinstance(created, dict) and numeric_id(created.get("id")):
            release_id = created["id"]
        require(release_id is not None and release_id not in existing_ids,
                "Malformed or reused new release ID")
        require(created.get("tag_name") == tag and created.get("name") == tag
                and created.get("target_commitish") == sha
                and created.get("draft") is False and created.get("prerelease") is False
                and created.get("body") in ("", None), "Malformed new public release response")
        upload_url = f"https://uploads.github.com/repos/{REPOSITORY}/releases/{release_id}/assets"
        require(created.get("upload_url") == upload_url + "{?name,label}",
                "Malformed new release upload URL")
        for path in assets:
            upload_attempted = path.name
            result = api.api("POST", upload_url + "?name=" + quote(path.name, safe=""), asset=path)
            require(isinstance(result, dict) and numeric_id(result.get("id"))
                    and result.get("name") == path.name.replace(" ", ".")
                    and result.get("state") == "uploaded"
                    and type(result.get("size")) is int and result["size"] == path.stat().st_size,
                    f"Malformed upload response for {path.name}")
            uploaded.append(path.name)
            upload_attempted = None
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        retained = [
            f"tag {tag} retained" if tag_created else
            f"tag {tag} creation may have succeeded" if tag_attempted else "tag not attempted",
            f"release ID {release_id} retained" if release_id is not None else
            "release creation may have succeeded; ID unknown" if release_attempted else
            "release not attempted",
            "uploaded assets retained: " + (", ".join(uploaded) or "(none confirmed)"),
        ]
        if upload_attempted:
            retained.append(f"upload of {upload_attempted} may have succeeded")
        raise ReleaseError(f"{error}. Manual recovery required: {'; '.join(retained)}. "
                           "No retry, rollback, update or deletion performed.") from error
    print(f"Published {tag} at {sha}; new release ID {release_id}; assets: {', '.join(uploaded)}")
    return release_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "check", "publish"))
    parser.add_argument("--sha", required=True)
    parser.add_argument("assets", nargs="*")
    args = parser.parse_intermixed_args(argv)
    if args.command == "publish":
        publish(args.sha, args.assets)
    else:
        if args.assets:
            parser.error("preflight/check does not accept assets")
        version, tag = (preflight if args.command == "preflight" else check)(args.sha)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"version={version}\ntag={tag}\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Publication stopped: {error}. Inspect retained state; no rollback/retry.",
              file=sys.stderr)
        sys.exit(1)
