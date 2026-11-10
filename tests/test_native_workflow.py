"""Run real workflow Bash behind closed-PATH native/credential stubs."""

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import unittest

import yaml

from helper_support import Fixture, ROOT, cwd, original, source
import native


WORKFLOW = ROOT / ".github/workflows/release.yml"
PRODUCT = "Continuity Camera"
CONTENTS = native.BUNDLE / "Contents"

STUB = r'''#!PYTHON
import base64, json, os, pathlib, plistlib, shutil, sys
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["NATIVE_LOG"], "a") as stream:
    stream.write(json.dumps([tool, *args]) + "\n")
if os.environ.get("FAIL_TOOL") == tool:
    sys.exit(23)
contents = pathlib.Path("test/Plugins/Continuity Camera.bundle/Contents")
if tool in ("python", "python3"):
    if args and args[0].endswith("release.py"):
        assert os.environ["GH_TOKEN"] == "offline-only"
        assert os.environ["GITHUB_REPOSITORY"] == "miyako/4d-plugin-continuity-camera"
        assert args[2:4] == ["--sha", os.environ["GITHUB_SHA"]]
        if args[1] == "publish":
            assert args[4:] == [f"Continuity Camera-{os.environ['VERSION']}.zip",
                                f"Continuity Camera-{os.environ['VERSION']}.dmg"]
        if os.environ.get("FAIL_PUBLICATION") or (
                args[1] == "preflight" and os.environ.get("FAIL_PREFLIGHT")):
            sys.exit(9)
    else:
        os.execv(sys.executable, [sys.executable, *args])
elif tool == "xcodebuild":
    assert args == ["-project", "Continuity Camera.xcodeproj", "-target", "Continuity Camera",
                    "-configuration", "Default", "ARCHS=arm64 x86_64", "ONLY_ACTIVE_ARCH=NO",
                    "MACOSX_DEPLOYMENT_TARGET=11.0", "build"]
    for relative in ("MacOS/Continuity Camera", "Info.plist", "Resources/manifest.json",
                     "Resources/constants.xlf", "Resources/English.lproj/InfoPlist.strings",
                     "_CodeSignature/CodeResources"):
        assert not (contents / relative).exists(), relative
    if not os.environ.get("STALE_BUILD"):
        (contents / "MacOS").mkdir(parents=True, exist_ok=True)
        (contents / "MacOS/Continuity Camera").write_bytes(b"fresh-macos")
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleShortVersionString": os.environ["SOURCE_VERSION"]
        }, fmt=plistlib.FMT_BINARY))
        (contents / "Resources/English.lproj").mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(pathlib.Path(".github/scripts").resolve()))
        sys.dont_write_bytecode = True
        import source_version
        (contents / "Resources/English.lproj/InfoPlist.strings").write_bytes(
            plistlib.dumps(source_version.built_dictionary("English.lproj/InfoPlist.strings"),
                           fmt=plistlib.FMT_BINARY))
        for resource in ("manifest.json", "constants.xlf"):
            shutil.copyfile(resource, contents / "Resources" / resource)
        db = pathlib.Path("test/Plugins/XCBuildData")
        db.mkdir(parents=True, exist_ok=True)
        for name in ("build.db", "build.db-wal", "build.db-shm"):
            (db / name).write_bytes(b"fresh-database")
elif tool == "lipo":
    print(os.environ.get("DYLIB_ARCHES" if args[-1].endswith(".dylib") else "ARCHES",
                         "arm64 x86_64"))
elif tool == "base64":
    pathlib.Path(args[args.index("-o") + 1]).write_bytes(base64.b64decode(sys.stdin.read()))
elif tool == "security":
    if args[0] == "find-identity":
        print("" if os.environ.get("MISSING_IDENTITY") else
              '  1) OFFLINE "Developer ID Application: Fixture"')
elif tool == "codesign":
    if "--verify" in args and os.environ.get("FAIL_VERIFY"):
        sys.exit(7)
    if "--verify" not in args:
        (contents / "_CodeSignature").mkdir(exist_ok=True)
        (contents / "_CodeSignature/CodeResources").write_bytes(b"fresh-signature")
elif tool in ("ditto", "hdiutil"):
    suffix = "zip" if tool == "ditto" else "dmg"
    assert args[-1] == f"Continuity Camera-{os.environ['VERSION']}.{suffix}"
    pathlib.Path(args[-1]).write_bytes(b"offline-package")
elif tool == "xcrun":
    if args[:2] == ["notarytool", "submit"]:
        response = os.environ.get("NOTARY_JSON")
        print(response if response is not None else json.dumps(
            {"id": "offline-id", "status": os.environ.get("NOTARY_STATUS", "Accepted")}))
        sys.exit(int(os.environ.get("NOTARY_EXIT", "0")))
    elif args[:2] == ["stapler", "staple"]:
        sys.exit(int(os.environ.get("STAPLE_EXIT", "0")))
    else:
        assert args[:2] == ["notarytool", "store-credentials"], args
else:
    sys.exit("Unexpected stub: " + tool)
'''


class NativeWorkflowTests(Fixture):
    def setUp(self):
        super().setUp()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.bin = self.root / "tools"
        self.bin.mkdir()
        self.log = self.root / "native.jsonl"
        for tool in ("python", "python3", "xcodebuild", "lipo", "base64",
                     "security", "codesign", "ditto", "hdiutil", "xcrun"):
            path = self.bin / tool
            path.write_text(STUB.replace("PYTHON", sys.executable))
            path.chmod(0o755)
        # No developer/credential tools or shell profiles are inherited.
        for tool in ("bash", "grep", "head", "sed"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        version = source.read(ROOT)
        self.env = {
            "PATH": str(self.bin), "HOME": str(self.checkout), "LC_ALL": "C",
            "RUNNER_TEMP": str(self.checkout), "NATIVE_LOG": str(self.log),
            "SOURCE_VERSION": version, "PRODUCT_NAME": PRODUCT, "BASE_DIR": "test",
            "MACOS_DEPLOYMENT_TARGET": "11.0", "VERSION": version,
            "KEYCHAIN_PATH": str(self.checkout / "offline.keychain"),
            "BUNDLE_PATH": str(native.BUNDLE), "GITHUB_ENV": str(self.checkout / "github-env"),
            "GITHUB_SHA": "a" * 40, "GH_TOKEN": "offline-only",
            "GITHUB_REPOSITORY": "miyako/4d-plugin-continuity-camera",
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "CERT_BASE64": "b2ZmbGluZQ==", "CERT_PASSWORD": "offline-only",
            "KEYCHAIN_PASSWORD": "offline-only", "APPLE_ID": "offline@example.invalid",
            "TEAM_ID": "OFFLINEONLY", "APP_PASSWORD": "offline-only",
        }
        shutil.copytree(ROOT / ".github/scripts", self.checkout / ".github/scripts")
        self.sources(self.checkout)
        self.assertEqual(source.read(self.checkout), version)
        for relative in ("manifest.json", "constants.xlf"):
            (self.checkout / relative).write_bytes(original(relative))
        self.jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
        self.contents = self.checkout / CONTENTS
        self.protected = {}
        for relative in ("MacOS/runtime.dylib", "QuickLook/Thumbnail.png",
                         "Windows/Continuity Camera.4DX", "Windows64/Continuity Camera.4DX",
                         "Resources/custom.txt"):
            path = self.contents / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preserved")
            self.protected[path] = b"preserved"
        for relative in native.FILES:
            path = self.checkout / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"stale")
        for relative in ("test/Plugins/Default/keep", "build/unrelated/keep",
                         "a/libjsoncpp.a", "lib/windows64/jsoncpp.lib"):
            path = self.checkout / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preserved")
            self.protected[path] = b"preserved"

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_step(self, job, name, success=True):
        step = next(step for step in self.jobs[job]["steps"] if step.get("name") == name)
        script = step["run"]
        self.assertNotIn("${{", script, "Untrusted contexts must not be spliced into shell programs")
        self.assertEqual(step.get("shell", "bash"), "bash")
        command = ["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script]
        result = subprocess.run(command, cwd=self.checkout, env=self.env,
                                capture_output=True, text=True, timeout=30)
        if success is True:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        elif success is False:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def mac_step(self, name, success=True):
        return self.run_step("build-macos", name, success)

    def build(self):
        self.mac_step("build macOS plugin (universal)")

    def pipeline(self):
        for job in ("version", "build-macos"):
            for step in self.jobs[job]["steps"]:
                if "run" not in step:
                    continue
                result = self.run_step(job, step["name"], success=None)
                if result.returncode:
                    return result
        return result

    def test_complete_real_shell_pipeline_is_fresh_and_publishes_only_after_staple(self):
        result = self.pipeline()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.contents / "MacOS/Continuity Camera").read_bytes(), b"fresh-macos")
        self.assertEqual((self.contents / "Windows64/Continuity Camera.4DX").read_bytes(), b"preserved")
        self.assertEqual((self.contents / "_CodeSignature/CodeResources").read_bytes(), b"fresh-signature")
        for path, data in self.protected.items():
            self.assertEqual(path.read_bytes(), data)
        calls = self.calls()
        index = lambda test: next(i for i, call in enumerate(calls) if test(call))
        xcode = index(lambda call: call[0] == "xcodebuild")
        sign = index(lambda call: call[0] == "codesign")
        package = index(lambda call: call[0] == "ditto")
        staple = index(lambda call: call[:3] == ["xcrun", "stapler", "staple"])
        publish = index(lambda call: call[0] == "python3" and "publish" in call)
        self.assertLess(xcode, sign)
        self.assertLess(sign, package)
        self.assertLess(package, staple)
        self.assertLess(staple, publish)
        self.assertEqual(sum(call[:3] == ["xcrun", "notarytool", "submit"] for call in calls), 2)
        self.assertTrue(all("--strict" in call and "--deep" in call for call in calls
                            if call[0] == "codesign" and "--verify" in call))

    def test_pipeline_uses_selected_source_version_not_the_intake_literal(self):
        before, version = self.env["VERSION"], "3.2.1"
        for path in source.SOURCE_PATHS:
            target = self.checkout / path
            target.write_bytes(target.read_bytes().replace(before.encode(), version.encode()))
        self.env.update(SOURCE_VERSION=version, VERSION=version)
        self.assertEqual(source.read(self.checkout), version)
        result = self.pipeline()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for suffix in ("zip", "dmg"):
            self.assertTrue((self.checkout / f"Continuity Camera-{version}.{suffix}").is_file())

    def test_cleanup_error_never_reaches_xcode(self):
        path = self.contents / "Info.plist"
        path.unlink()
        path.mkdir()
        self.mac_step("build macOS plugin (universal)", success=False)
        self.assertFalse(any(call[0] == "xcodebuild" for call in self.calls()))

    def test_missing_empty_stale_binary_and_exact_architecture_tokens(self):
        self.env["STALE_BUILD"] = "1"
        self.build()
        self.mac_step("verify universal binary", success=False)
        self.env.pop("STALE_BUILD")
        self.build()
        binary = self.contents / "MacOS/Continuity Camera"
        binary.write_bytes(b"")
        self.mac_step("verify universal binary", success=False)
        binary.write_bytes(b"fresh")
        for arches in ("arm64e x86_64", "arm64 x86_64h", ""):
            self.env["ARCHES"] = arches
            self.mac_step("verify universal binary", success=False)
        self.env["ARCHES"] = "arm64 x86_64 arm64e i386"
        self.mac_step("verify universal binary")
        # The original workflow did not inspect packaged runtime libraries.
        self.env["DYLIB_ARCHES"] = "arm64e x86_64"
        self.mac_step("verify universal binary")

    def test_missing_and_drifted_fresh_metadata_or_resources_rejected(self):
        self.build()
        for relative in ("Info.plist", "Resources/English.lproj/InfoPlist.strings",
                         "Resources/constants.xlf", "Resources/manifest.json"):
            path = self.contents / relative
            saved = path.read_bytes()
            for raw in (None, b"", b"drift"):
                with self.subTest(relative=relative, raw=raw):
                    if raw is None:
                        path.unlink()
                    else:
                        path.write_bytes(raw)
                    self.mac_step("verify universal binary", success=False)
                    path.write_bytes(saved)

    def test_all_credential_native_arch_sign_notary_and_staple_failures_never_publish(self):
        cases = [
            {"FAIL_TOOL": tool} for tool in ("python3", "base64", "security",
                                           "xcrun", "xcodebuild", "lipo", "codesign",
                                           "ditto", "hdiutil")
        ] + [
            {"FAIL_PREFLIGHT": "1"}, {"MISSING_IDENTITY": "1"}, {"FAIL_VERIFY": "1"},
            {"STALE_BUILD": "1"},
            {"ARCHES": "arm64e x86_64"},
            {"NOTARY_EXIT": "5", "NOTARY_STATUS": "Accepted"},
            {"NOTARY_STATUS": "Invalid"}, {"NOTARY_STATUS": ""},
            {"NOTARY_JSON": "not json"}, {"NOTARY_JSON": "[]"},
            {"NOTARY_JSON": "{}"}, {"STAPLE_EXIT": "8"},
            {"NOTARY_JSON": '{"status":"Invalid","status":"Accepted"}'},
            {"NOTARY_JSON": '{"status":"Accepted","extra":{"id":1,"id":2}}'},
            {"NOTARY_JSON": '{"status":"Accepted","extra":NaN}'},
            {"NOTARY_JSON": '{"status":"Accepted","extra":Infinity}'},
            {"NOTARY_JSON": '{"status":"Accepted","extra":-Infinity}'},
            {"NOTARY_JSON": '{"status":"Accepted","extra":1e999}'},
        ]
        original_env = dict(self.env)
        for case in cases:
            with self.subTest(case=case):
                self.env = {**original_env, **case}
                self.log.unlink(missing_ok=True)
                for suffix in ("zip", "dmg"):
                    (self.checkout / f"Continuity Camera-{self.env['VERSION']}.{suffix}").unlink(missing_ok=True)
                result = self.pipeline()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(any(call[0] == "python3" and "publish" in call
                                     for call in self.calls()))

    def physical_snapshot(self):
        result = {}
        def visit(path):
            info = path.lstat()
            relative = str(path.relative_to(self.checkout))
            if path.is_symlink():
                result[relative] = (info.st_mode, os.readlink(path))
                return
            mode = info.st_mode & 0o777
            try:
                path.chmod(mode | 0o700)
                result[relative] = (info.st_mode, path.read_bytes() if path.is_file() else None)
                if path.is_dir():
                    for child in path.iterdir():
                        visit(child)
            finally:
                path.chmod(mode)
        for path in self.checkout.iterdir():
            visit(path)
        return result

    def test_extracted_cleanup_every_type_access_nested_git_before_any_delete_same_fixture_restored(self):
        intermediate = self.checkout / native.DIRECTORIES[0]
        (intermediate / "nested").mkdir(parents=True)
        (intermediate / "nested/last.o").write_bytes(b"later-intermediate")
        seed = self.root / "seed"
        shutil.copytree(self.checkout, seed)
        cases = [("leaf-" + kind, native.FILES[-1], kind)
                 for kind in ("directory", "fifo", "socket", "symlink", "unreadable")]
        cases += [("intermediate-" + kind, native.DIRECTORIES[0], kind)
                  for kind in ("file", "symlink", "search-only", "unreadable", "nonwritable")]
        cases += [("enclosing-" + kind, Path("test/Plugins"), kind)
                  for kind in ("search-only", "unreadable", "nonwritable", "symlink", "file")]
        cases += [("descendant-" + kind, native.DIRECTORIES[0] / "nested", kind)
                  for kind in ("search-only", "unreadable", "nonwritable", "symlink", "fifo", "socket")]
        directories = (
            Path("test"), Path("test/Plugins"), native.BUNDLE, CONTENTS,
            CONTENTS / "Resources", CONTENTS / "Resources/English.lproj",
            CONTENTS / "_CodeSignature", native.DIRECTORIES[0],
            native.DIRECTORIES[0] / "nested",
        )
        cases += [("git-" + kind + "-" + str(parent), parent / ".git", kind)
                  for parent in directories for kind in ("marker-file", "marker-dir", "marker-link")]
        for label, relative, kind in cases:
            with self.subTest(case=label):
                shutil.rmtree(self.checkout)
                shutil.copytree(seed, self.checkout)
                self.log.unlink(missing_ok=True)
                target = self.checkout / relative
                saved = self.root / "saved"
                if target.exists():
                    target.rename(saved)
                target.parent.mkdir(parents=True, exist_ok=True)
                if kind in ("directory", "marker-dir"):
                    target.mkdir()
                elif kind in ("file", "marker-file"):
                    target.write_bytes(b"wrong-type-or-marker")
                elif kind in ("symlink", "marker-link"):
                    target.symlink_to(saved if saved.exists() else self.root / "missing-marker")
                elif kind == "fifo":
                    os.mkfifo(target)
                elif kind == "socket":
                    with cwd(target.parent), socket.socket(socket.AF_UNIX) as node:
                        node.bind(target.name)
                else:
                    saved.rename(target)
                    target.chmod({"search-only": 0o111, "unreadable": 0, "nonwritable": 0o555}[kind])
                before = self.physical_snapshot()
                self.mac_step("build macOS plugin (universal)", success=False)
                self.assertEqual(self.physical_snapshot(), before)
                self.assertFalse(any(call[0] == "xcodebuild" or "publish" in call
                                     for call in self.calls()))
                if kind in ("search-only", "unreadable", "nonwritable"):
                    target.chmod((seed / relative).stat().st_mode & 0o777)
                elif target.is_dir() and not target.is_symlink():
                    target.rmdir()
                else:
                    target.unlink()
                if saved.exists():
                    saved.rename(target)
                self.log.unlink(missing_ok=True)
                self.build()
                self.assertTrue(any(call[0] == "xcodebuild" for call in self.calls()))
                for path, data in self.protected.items():
                    self.assertEqual(path.read_bytes(), data)


class NativeWorkflowContractTests(unittest.TestCase):
    def test_both_manual_workflows_have_exact_inputs_immutable_checkouts_and_no_linkage(self):
        workflows = {
            path.name: yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            for path in WORKFLOW.parent.glob("*.yml")
        }
        self.assertEqual(set(workflows), {"release.yml", "bump-version.yml"})
        build, bump = workflows["release.yml"], workflows["bump-version.yml"]
        self.assertEqual(build["name"], "Build")
        self.assertEqual(build["on"], {"workflow_dispatch": ""})
        self.assertEqual(bump["name"], "Bump version")
        self.assertEqual(set(bump["on"]), {"workflow_dispatch"})
        inputs = bump["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"mode"})
        self.assertEqual(inputs["mode"]["type"], "choice")
        self.assertEqual(inputs["mode"]["options"], ["patch", "minor", "major"])
        self.assertEqual(inputs["mode"]["default"], "patch")
        self.assertEqual(inputs["mode"]["required"], "true")
        self.assertEqual(build["concurrency"],
                         {"group": "release-publication", "cancel-in-progress": "false"})
        self.assertNotEqual(bump["concurrency"]["group"], build["concurrency"]["group"])
        self.assertEqual(bump["concurrency"]["cancel-in-progress"], "false")
        for workflow in workflows.values():
            for job in workflow["jobs"].values():
                self.assertNotIn("if", job)
                checkouts = [step for step in job["steps"]
                             if step.get("uses", "").startswith("actions/checkout@")]
                self.assertEqual(len(checkouts), 1)
                self.assertEqual(checkouts[0]["with"]["ref"], "${{ github.sha }}")
                for step in job["steps"]:
                    self.assertNotIn("${{", step.get("run", ""))
        bump_runs = [step["run"] for job in bump["jobs"].values()
                     for step in job["steps"] if "run" in step]
        self.assertEqual(bump_runs, [
            'python3 .github/scripts/bump.py "$MODE" --sha "$EVENT_SHA" --ref "$EVENT_REF"'
        ])
        for workflow in workflows.values():
            for job in workflow["jobs"].values():
                for step in job["steps"]:
                    self.assertNotIn("workflow run", step.get("run", ""))
                    self.assertNotIn("repository_dispatch", str(step))
                    self.assertNotIn("workflow_dispatch", str(step))

    def test_manual_immutable_cross_branch_workflow_and_topology(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["name"], "Build")
        self.assertEqual(workflow["on"], {"workflow_dispatch": ""})
        self.assertEqual(workflow["concurrency"],
                         {"group": "release-publication", "cancel-in-progress": "false"})
        self.assertEqual(workflow["env"]["PRODUCT_NAME"], PRODUCT)
        self.assertEqual(workflow["env"]["BASE_DIR"], "test")
        self.assertEqual(workflow["env"]["MACOS_DEPLOYMENT_TARGET"], "11.0")
        before = yaml.load(original(".github/workflows/release.yml"), Loader=yaml.BaseLoader)
        self.assertEqual(list(workflow["jobs"]), list(before["jobs"]))
        for name, job in workflow["jobs"].items():
            self.assertEqual(job["runs-on"], before["jobs"][name]["runs-on"])
            self.assertEqual(job.get("needs"), before["jobs"][name].get("needs"))
            self.assertNotIn("if", job)
            for step in job["steps"]:
                self.assertNotIn("if", step)
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertEqual(step["with"]["ref"], "${{ github.sha }}")
        version = workflow["jobs"]["version"]["steps"][-1]
        self.assertEqual(version["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertEqual(version["run"],
                         'python3 .github/scripts/release.py preflight --sha "$GITHUB_SHA"')
        mac = workflow["jobs"]["build-macos"]["steps"]
        self.assertIn("release.py preflight", mac[1]["run"])
        self.assertIn("release.py publish", mac[-1]["run"])
        self.assertEqual(mac[-1]["env"]["GH_TOKEN"], "${{ github.token }}")
        combined = WORKFLOW.read_text()
        for forbidden in ("inputs.", "github.ref", "softprops", "dispatches", "bump.py"):
            self.assertNotIn(forbidden, combined)

    def test_native_projects_dependencies_and_committed_package_preserved(self):
        path = "Continuity Camera.xcodeproj/project.pbxproj"
        normalize = lambda data: re.sub(
            rb"((?:MARKETING_VERSION|CURRENT_PROJECT_VERSION)\s*=\s*)[0-9.]+;",
            rb"\g<1>VERSION;", data)
        self.assertEqual(normalize((ROOT / path).read_bytes()), normalize(original(path)))
        for path in ("Continuity Camera.vcxproj", "Continuity Camera.sln", "constants.xlf", "manifest.json",
                     str(CONTENTS / "Windows/Continuity Camera.4DX"),
                     str(CONTENTS / "Windows64/Continuity Camera.4DX"),
                     *(str(path) for path in native.FILES[:-1]),
                     str(CONTENTS / "QuickLook/Thumbnail.png")):
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_bytes(), original(path))
