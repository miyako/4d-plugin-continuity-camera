"""Byte-preserving coordinated metadata tests against immutable intake and live source."""

from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
from unittest.mock import patch

from helper_support import Fixture, ROOT, original, source


class SourceVersionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.sources()
        self.version = source.read(self.root)

    def snapshot(self):
        return {path: ((self.root / path).read_bytes(), stat.S_IMODE((self.root / path).stat().st_mode))
                for path in source.SOURCE_PATHS if (self.root / path).exists()}

    def reject(self):
        before = self.snapshot()
        with patch.object(Path, "write_bytes") as write:
            with self.assertRaises((ValueError, UnicodeError, OSError)):
                source.read(self.root)
            with self.assertRaises((ValueError, UnicodeError, OSError)):
                source.bump("patch", self.root)
        write.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def modify(self, path, transform):
        target = self.root / path
        target.write_bytes(transform(target.read_bytes()))

    def plist(self, **fields):
        target = self.root / source.PLIST
        data = plistlib.loads(target.read_bytes())
        data.update(fields)
        target.write_bytes(plistlib.dumps(data))

    def test_exact_intake_authority_and_serialization(self):
        self.assertEqual(source.SETTING_COUNT, 6)
        data = original(source.LOCALIZED)
        self.assertIn(b"\r", data)
        self.assertNotIn(b"\n", data)
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", original(source.PLIST))
        plist = plistlib.loads(original(source.PLIST))
        self.assertEqual(plist[source.SHORT], "$(MARKETING_VERSION)")
        self.assertNotIn(source.BUILD, plist)
        fields = source.project_settings(original(source.PROJECT).decode())
        for key in source.KEYS:
            expected = "2.0.3" if key == "MARKETING_VERSION" else "2.0.0"
            self.assertEqual([value for value, _, _ in fields[key]], [expected] * 6)
        self.assertIn(b"Copyright 2022 miyako.", original(source.LOCALIZED))

    def test_immutable_coordinated_contract_and_narrow_source_repair(self):
        header = subprocess.check_output([
            "git", "show",
            "d90755699b187eaee14392043d0f37efc3ed27e7:.github/workflows/bump-version.yml",
        ], cwd=ROOT).decode()
        self.assertIn("MARKETING_VERSION/CURRENT_PROJECT_VERSION AND Info.plist", header)
        self.assertIn("CFBundleShortVersionString/CFBundleVersion", header)
        version = source.read(ROOT).encode()
        for path in source.SOURCE_PATHS:
            expected = original(path)
            if path == source.PROJECT:
                expected = expected.replace(
                    b"MARKETING_VERSION = 2.0.3;", b"MARKETING_VERSION = " + version + b";"
                ).replace(
                    b"CURRENT_PROJECT_VERSION = 2.0.0;",
                    b"CURRENT_PROJECT_VERSION = " + version + b";",
                )
            elif path == source.PLIST:
                expected = expected.replace(b"<string>2.0.3</string>",
                                            b"<string>" + version + b"</string>")
            elif path == source.LOCALIZED:
                expected = expected.replace(
                    b'CFBundleShortVersionString = "2.0.0";',
                    b'CFBundleShortVersionString = "' + version + b'";',
                ).replace(b"Continuity Camera version 2.0.0,", b"Continuity Camera version " + version + b",")
            self.assertEqual((ROOT / path).read_bytes(), expected, path)

    def test_live_and_stable_all_bump_modes_exact_bytes_modes_and_absence(self):
        for version in (self.version, "3.2.1"):
            for mode in ("major", "minor", "patch"):
                with self.subTest(version=version, mode=mode):
                    self.sources()
                    for path in source.SOURCE_PATHS:
                        self.modify(path, lambda data: data.replace(self.version.encode(), version.encode()))
                        (self.root / path).chmod(0o750)
                    before = self.snapshot()
                    parts = list(map(int, version.split(".")))
                    index = ("major", "minor", "patch").index(mode)
                    parts[index] += 1
                    parts[index + 1:] = [0] * (2 - index)
                    expected = ".".join(map(str, parts))
                    self.assertEqual(source.bump(mode, self.root), expected)
                    self.assertEqual(source.read(self.root), expected)
                    for path, (raw, filemode) in before.items():
                        self.assertEqual((self.root / path).read_bytes(),
                                         raw.replace(version.encode(), expected.encode()))
                        self.assertEqual(stat.S_IMODE((self.root / path).stat().st_mode), filemode)
                    self.assertNotIn(source.BUILD, plistlib.loads((self.root / source.PLIST).read_bytes()))

    def test_canonical_versions_missing_extra_drift_and_cycles_never_write(self):
        for key in source.KEYS:
            for value in ("1.2", "7", "01.2.3", "v1.2.3", "1.2.3-rc", "$(UNKNOWN)", "",
                          f"$({key})", f"${{{key}}}", "900.800.700"):
                with self.subTest(key=key, value=value):
                    self.sources()
                    self.modify(source.PROJECT, lambda data: data.replace(
                        f"{key} = {self.version};".encode(), f"{key} = {value};".encode(), 1))
                    self.reject()
            self.sources()
            self.modify(source.PROJECT, lambda data: data.replace(
                f"{key} = {self.version};".encode(), b"", 1))
            self.reject()
            self.sources()
            self.modify(source.PROJECT, lambda data: data + f"\n{key} = {self.version};".encode())
            self.reject()
        self.sources()
        self.modify(source.PROJECT, lambda data: data.replace(
            f"MARKETING_VERSION = {self.version};".encode(),
            b"MARKETING_VERSION = $(CURRENT_PROJECT_VERSION);").replace(
            f"CURRENT_PROJECT_VERSION = {self.version};".encode(),
            b"CURRENT_PROJECT_VERSION = ${MARKETING_VERSION};"))
        self.reject()

    def test_duplicate_replacing_missing_assignment_still_rejected(self):
        for quoted in (False, True):
            self.sources()
            raw = (self.root / source.PROJECT).read_bytes()
            token = f"MARKETING_VERSION = {self.version};".encode()
            replacement = (f'"MARKETING_VERSION" = "{self.version}";' if quoted
                           else token.decode()).encode()
            first = raw.index(token)
            second = raw.index(token, first + len(token))
            raw = raw[:second] + raw[second:].replace(token, b"", 1)
            raw = raw[:first] + replacement + b" " + raw[first:]
            (self.root / source.PROJECT).write_bytes(raw)
            self.reject()

    def test_quoted_inline_scoped_assignments_and_noise(self):
        value = self.version
        project = "/* ignored MARKETING_VERSION = 0.0.0; */\n{\n"
        for identifier, name in source.CONFIGURATIONS.items():
            project += (f'{identifier} = {{ isa = XCBuildConfiguration; name = {name}; '
                        'buildSettings = { OTHER = "CURRENT_PROJECT_VERSION = 9.9.9;"; '
                        f'"MARKETING_VERSION" = "{value}"; CURRENT_PROJECT_VERSION = {value}; }}; }};\r\n')
        project += "}\n// MARKETING_VERSION = 8.8.8;\r"
        (self.root / source.PROJECT).write_bytes(project.encode())
        new = source.bump("patch", self.root)
        self.assertEqual((self.root / source.PROJECT).read_bytes(),
                         project.replace(value, new).encode())
        for noise in ("/* unterminated", '"unterminated', "}", "{"):
            self.sources()
            self.modify(source.PROJECT, lambda data: data + noise.encode())
            self.reject()

    def test_configuration_names_and_identity_drift_fail_without_writes(self):
        for old, new in ((b"name = Default;", b"name = Debug;"),
                         (b"42D60B400A9DDBC8002ABB42", b"42D60B400A9DDBC8002ABB43"),
                         (b"isa = XCBuildConfiguration;", b"isa = PBXGroup;")):
            self.sources()
            self.modify(source.PROJECT, lambda raw: raw.replace(old, new))
            self.reject()

    def test_recursive_references_both_syntaxes_preserved(self):
        for referencing, target in (source.KEYS, tuple(reversed(source.KEYS))):
            for reference in (f"$({target})", f"${{{target}}}"):
                for quoted in (False, True):
                    self.sources()
                    replacement = f'"{reference}"' if quoted else reference
                    self.modify(source.PROJECT, lambda data: data.replace(
                        f"{referencing} = {self.version};".encode(),
                        f"{referencing} = {replacement};".encode()))
                    for plist_ref in source.REFERENCES:
                        self.plist(**{source.SHORT: plist_ref, source.BUILD: plist_ref})
                        new = source.bump("patch", self.root)
                        self.assertIn(reference.encode(), (self.root / source.PROJECT).read_bytes())
                        actual = plistlib.loads((self.root / source.PLIST).read_bytes())
                        self.assertEqual(actual[source.SHORT], plist_ref)
                        self.assertEqual(actual[source.BUILD], plist_ref)
                        self.assertEqual(source.read(self.root), new)
                        # Re-establish the selected fixture for the next reference.
                        self.sources()
                        self.modify(source.PROJECT, lambda data: data.replace(
                            f"{referencing} = {self.version};".encode(),
                            f"{referencing} = {replacement};".encode()))

    def test_required_short_optional_build_and_invalid_plist_zero_writes(self):
        for key in (source.SHORT, source.BUILD):
            for bad in (True, 123, [], {}, "", "$(UNKNOWN)", "01.2.3", "900.800.700"):
                self.sources()
                self.plist(**{key: bad})
                self.reject()

        for fields in ({}, {source.BUILD: self.version}):
            self.sources()
            (self.root / source.PLIST).write_bytes(plistlib.dumps(fields))
            self.reject()
        self.sources()
        self.plist(**{source.BUILD: self.version})
        new = source.bump("minor", self.root)
        self.assertEqual(plistlib.loads((self.root / source.PLIST).read_bytes())[source.BUILD], new)
        for raw in (b"not plist", plistlib.dumps([]),
                    plistlib.dumps({source.SHORT: self.version}, fmt=plistlib.FMT_BINARY)):
            self.sources()
            (self.root / source.PLIST).write_bytes(raw)
            self.reject()

    def test_missing_short_alone_fails_read_and_bump_without_any_write(self):
        fields = plistlib.loads((self.root / source.PLIST).read_bytes())
        fields[source.BUILD] = self.version
        del fields[source.SHORT]
        (self.root / source.PLIST).write_bytes(plistlib.dumps(fields))
        self.reject()

    def test_both_plist_version_keys_missing_fails_without_any_write(self):
        fields = plistlib.loads((self.root / source.PLIST).read_bytes())
        fields.pop(source.SHORT, None)
        fields.pop(source.BUILD, None)
        (self.root / source.PLIST).write_bytes(plistlib.dumps(fields))
        self.reject()

    def test_duplicate_xml_localized_keys_and_unknown_serialization(self):
        for key in (source.SHORT, source.BUILD, "CFBundleIdentifier"):
            self.sources()
            self.plist(**{key: self.version})
            self.modify(source.PLIST, lambda raw: raw.replace(
                b"</dict>", f"<key>{key}</key><string>{self.version}</string></dict>".encode()))
            self.reject()
        for key in (source.SHORT, source.INFO, "CFBundleName"):
            for quoted in (False, True):
                self.sources()
                token = f'"{key}"' if quoted else key
                self.modify(source.LOCALIZED, lambda raw: raw + f'{token} = "{self.version}";'.encode())
                self.reject()
        for old, new in (("Copyright 2022", "Copyright 2025"),
                         ("Continuity Camera version ", "Other Product version ")):
            self.sources()
            self.modify(source.LOCALIZED, lambda raw: raw.replace(old.encode(), new.encode()))
            self.reject()
        for raw in (b"broken", b'CFBundleShortVersionString = "unterminated'):
            self.sources()
            (self.root / source.LOCALIZED).write_bytes(raw)
            self.reject()

    def test_encoding_newlines_quotes_comments_unrelated_bytes(self):
        encodings = (("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"),
                     ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
                     ("utf-16-le", b""), ("utf-16-be", b""))
        for codec, bom in encodings:
            for ending in ("\r", "\n", "\r\n", "mixed"):
                self.sources()
                originals = {}
                for path in source.SOURCE_PATHS:
                    value = source.text(self.root / path)
                    if path == source.PLIST and codec.startswith("utf-16"):
                        value = value.replace('encoding="UTF-8"', 'encoding="UTF-16"')
                    lines = value.splitlines()
                    value = "".join(line + (("\r", "\r\n", "\n")[i % 3]
                                            if ending == "mixed" else ending)
                                    for i, line in enumerate(lines))
                    raw = bom + value.encode(codec)
                    (self.root / path).write_bytes(raw)
                    originals[path] = raw
                new = source.bump("patch", self.root)
                for path, raw in originals.items():
                    self.assertEqual((self.root / path).read_bytes(),
                                     raw.replace(self.version.encode(codec), new.encode(codec)))
        self.sources()
        self.plist(**{"UnrelatedVersion": self.version})
        noise = f"<!-- <key>{source.SHORT}</key><string>9.9.9</string> -->"
        self.modify(source.PLIST, lambda raw: raw.replace(b"</dict>", noise.encode() + b"</dict>"))
        before = (self.root / source.PLIST).read_bytes()
        source.bump("patch", self.root)
        self.assertIn(f"<string>{self.version}</string>".encode(),
                      (self.root / source.PLIST).read_bytes())
        self.assertEqual(before.replace(
            (f"<key>{source.SHORT}</key>\n\t<string>{self.version}</string>").encode(),
            (f"<key>{source.SHORT}</key>\n\t<string>" + source.read(self.root) + "</string>").encode()),
            (self.root / source.PLIST).read_bytes())

    def test_missing_source_invalid_encoding_and_mode_never_write(self):
        for path in source.SOURCE_PATHS:
            self.sources()
            (self.root / path).unlink()
            self.reject()
            self.sources()
            (self.root / path).write_bytes(b"\xffbroken")
            self.reject()
        self.sources()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            source.bump("other", self.root)
        self.assertEqual(before, self.snapshot())

    def test_localized_version_references_and_optional_build_preserved(self):
        self.modify(source.LOCALIZED, lambda raw: raw.replace(
            f'{source.SHORT} = "{self.version}"'.encode(),
            f'{source.SHORT} = "$(MARKETING_VERSION)"'.encode())
            + b'CFBundleVersion = "${CURRENT_PROJECT_VERSION}";\r')
        source.bump("patch", self.root)
        raw = (self.root / source.LOCALIZED).read_bytes()
        self.assertIn(b'$(MARKETING_VERSION)', raw)
        self.assertIn(b'${CURRENT_PROJECT_VERSION}', raw)

    def test_built_binary_xml_utf16_metadata_and_optional_absence(self):
        bundle = self.root / "bundle"
        contents = bundle / "Contents"
        localized = contents / "Resources" / source.LOCALIZED
        localized.parent.mkdir(parents=True)
        expected = {key: value[0] for key, value in
                    source.localized_fields(source.text(self.root / source.LOCALIZED)).items()}
        for fmt in (plistlib.FMT_BINARY, plistlib.FMT_XML):
            (contents / "Info.plist").write_bytes(plistlib.dumps(
                {source.SHORT: self.version}, fmt=fmt))
            for style in ("strings", "utf-16", "utf-16-be", "xml", "binary"):
                if style in ("xml", "binary"):
                    raw = plistlib.dumps(expected, fmt=(
                        plistlib.FMT_XML if style == "xml" else plistlib.FMT_BINARY))
                else:
                    value = source.text(self.root / source.LOCALIZED)
                    raw = value.encode("utf-8" if style == "strings" else style)
                    if style == "utf-16-be":
                        raw = b"\xfe\xff" + raw
                localized.write_bytes(raw)
                source.verify_bundle(bundle, self.root)
                localized.write_bytes(plistlib.dumps({**expected, source.INFO: "wrong"}, fmt=fmt))
                with self.assertRaises(ValueError):
                    source.verify_bundle(bundle, self.root)
        localized.write_bytes(plistlib.dumps(expected))
        for data in ({}, [], {source.SHORT: self.version, source.BUILD: self.version},
                     {source.SHORT: "900.800.700"}):
            (contents / "Info.plist").write_bytes(plistlib.dumps(data))
            with self.assertRaises(ValueError):
                source.verify_bundle(bundle, self.root)
        (contents / "Info.plist").write_bytes(
            f'{source.SHORT} = "{self.version}";'.encode())
        with self.assertRaises(ValueError):
            source.verify_bundle(bundle, self.root)

    def test_prebuilt_native_dependency_and_4d_counters_untouched(self):
        excluded = {
            "test/Plugins/Continuity Camera.bundle/Contents/Info.plist": b"old prebuilt",
            "test/Plugins/Continuity Camera.bundle/Contents/Resources/English.lproj/InfoPlist.strings":
                b"\xff\xfeold prebuilt",
            "manifest.json": b'{"version":"1.0","id":20000}',
            "constants.xlf": b'<xliff version="1.0"/>',
            "test/Project/Sources/catalog.4DCatalog": b"4D counter",
            "test/Plugins/Continuity Camera.bundle/Contents/Windows64/Continuity Camera.4DX": b"stub",
            "a/library.a": b"dependency",
        }
        for path, raw in excluded.items():
            self.write(path, raw)
        source.bump("major", self.root)
        for path, raw in excluded.items():
            self.assertEqual((self.root / path).read_bytes(), raw)

    def test_complete_localized_dictionary_text_encodings_xml_binary_and_version_references(self):
        bundle = self.root / "complete.bundle"
        localized = bundle / "Contents/Resources" / source.LOCALIZED
        localized.parent.mkdir(parents=True)
        (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(
            {source.SHORT: self.version}, fmt=plistlib.FMT_BINARY))
        expected = source.built_dictionary(self.root / source.LOCALIZED)
        forms = (
            ("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"),
            ("utf-16-le", b""), ("utf-16-le", b"\xff\xfe"),
            ("utf-16-be", b""), ("utf-16-be", b"\xfe\xff"),
            ("xml", b""), ("binary", b""),
        )
        for form, bom in forms:
            def serialize(fields):
                if form in ("xml", "binary"):
                    return plistlib.dumps(fields, fmt=(
                        plistlib.FMT_XML if form == "xml" else plistlib.FMT_BINARY))
                text = "\r".join(f'{key} = "{value}";' for key, value in fields.items())
                return bom + text.encode(form)
            for kind in ("changed-name", "missing-name", "extra-key"):
                with self.subTest(form=form, bom=bom, kind=kind):
                    changed = dict(expected)
                    if kind == "changed-name":
                        changed["CFBundleName"] = "Wrong display name"
                    elif kind == "missing-name":
                        del changed["CFBundleName"]
                    else:
                        changed["UnexpectedLabel"] = "not a version"
                    localized.write_bytes(serialize(changed))
                    with self.assertRaisesRegex(ValueError, "localized"):
                        source.verify_bundle(bundle, self.root)
                    localized.write_bytes(serialize(expected))
                    source.verify_bundle(bundle, self.root)
        original = (self.root / source.LOCALIZED).read_bytes()
        for key in (source.SHORT, source.BUILD):
            for reference in source.REFERENCES:
                with self.subTest(key=key, reference=reference):
                    raw = original.replace(
                        f'{source.SHORT} = "{self.version}"'.encode(),
                        f'{source.SHORT} = "{reference}"'.encode())
                    fields = dict(expected)
                    if key == source.BUILD:
                        raw += f'\r{source.BUILD} = "{reference}";'.encode()
                        fields[source.BUILD] = self.version
                    (self.root / source.LOCALIZED).write_bytes(raw)
                    localized.write_bytes(plistlib.dumps(fields, fmt=plistlib.FMT_BINARY))
                    source.verify_bundle(bundle, self.root)

    def test_actual_prebuilt_and_dependency_tree_unchanged_from_intake(self):
        diff = subprocess.check_output([
            "git", "diff", "--name-only",
            "014963d3c6c3ab23a8406b108fed3dee27836812", "--",
            "test", "a", "lib", "include", "4D Plugin API", "support",
            "manifest.json", "constants.json", "constants.xlf",
            "Continuity Camera.vcxproj", "Continuity Camera.sln", "Continuity Camera.zip", "Continuity Camera.dmg",
        ], cwd=ROOT)
        self.assertEqual(diff, b"")
