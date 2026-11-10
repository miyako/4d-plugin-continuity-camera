"""Validate coordinated source versions and edit only their original token spans."""

import argparse
from dataclasses import dataclass
from pathlib import Path
import plistlib
import re
from xml.parsers.expat import ExpatError


PROJECT = Path("Continuity Camera.xcodeproj/project.pbxproj")
PLIST = Path("Info.plist")
LOCALIZED = Path("English.lproj/InfoPlist.strings")
SOURCE_PATHS = (PROJECT, PLIST, LOCALIZED)
KEYS = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
SETTING_COUNT = 6
CONFIGURATIONS = {
    "42D60B400A9DDBC8002ABB42": "Development",
    "42D60B410A9DDBC8002ABB42": "Deployment",
    "42D60B420A9DDBC8002ABB42": "Default",
    "42D60B440A9DDBC8002ABB42": "Development",
    "42D60B450A9DDBC8002ABB42": "Deployment",
    "42D60B460A9DDBC8002ABB42": "Default",
}
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
REFERENCES = {f"$({key})": key for key in KEYS}
REFERENCES.update({f"${{{key}}}": key for key in KEYS})
SHORT, BUILD, INFO = "CFBundleShortVersionString", "CFBundleVersion", "CFBundleGetInfoString"
INFO_PREFIX = "Continuity Camera version "
INFO_SUFFIX = ", Copyright 2022 miyako."
PLIST_VERSION = re.compile(
    r"(<key>CFBundle(?:ShortVersionString|Version)</key>\s*<string>)([^<]*)(</string>)"
)
LOCALIZED_TOKEN = re.compile(
    r'\s+|/\*.*?\*/|//[^\r\n]*|'
    r'(?P<key>"[A-Za-z_][A-Za-z_0-9]*"|[A-Za-z_][A-Za-z_0-9]*)'
    r'\s*=\s*"(?P<value>(?:\\.|[^"\\])*)"\s*;', re.DOTALL,
)


class UniqueDictionary(dict):
    def __setitem__(self, key, value):
        if key in self:
            raise ValueError(f"Duplicate metadata key: {key}")
        super().__setitem__(key, value)


def decode(data):
    for bom, encoding in (
        (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
        (b"\xef\xbb\xbf", "utf-8"),
    ):
        if data.startswith(bom):
            value = data[len(bom):].decode(encoding)
            break
    else:
        bom, encoding = b"", "utf-8"
        if b"\0" in data:
            even, odd = data[::2].count(0), data[1::2].count(0)
            if len(data) % 2:
                raise ValueError("Malformed BOM-less source encoding")
            if odd > 2 * even:
                encoding = "utf-16-le"
            elif even > 2 * odd:
                encoding = "utf-16-be"
            else:
                raise ValueError("Ambiguous BOM-less source encoding")
        value = data.decode(encoding)
    if "\0" in value:
        raise ValueError("NUL characters are unsupported in metadata")
    return value, encoding, bom


def text(path):
    return decode(Path(path).read_bytes())[0]


@dataclass
class Token:
    value: str
    start: int
    end: int
    quoted: bool = False


def project_tokens(value):
    tokens, offset = [], 0
    while offset < len(value):
        if value[offset].isspace():
            offset += 1
        elif value.startswith("//", offset):
            match = re.search(r"[\r\n]", value[offset:])
            offset = len(value) if match is None else offset + match.start()
        elif value.startswith("/*", offset):
            end = value.find("*/", offset + 2)
            if end < 0:
                raise ValueError("Unterminated project comment")
            offset = end + 2
        elif value[offset] == '"':
            start = offset = offset + 1
            while offset < len(value) and value[offset] != '"':
                offset += 2 if value[offset] == "\\" else 1
            if offset >= len(value):
                raise ValueError("Unterminated project string")
            tokens.append(Token(value[start:offset], start, offset, True))
            offset += 1
        elif value.startswith(("$(", "${"), offset):
            close = ")" if value[offset + 1] == "(" else "}"
            end = value.find(close, offset + 2)
            if end < 0 or re.search(r"\s", value[offset:end]):
                raise ValueError("Malformed project reference")
            tokens.append(Token(value[offset:end + 1], offset, end + 1))
            offset = end + 1
        elif value[offset] in "{}=;(),":
            tokens.append(Token(value[offset], offset, offset + 1))
            offset += 1
        else:
            start = offset
            while offset < len(value) and not value[offset].isspace() \
                    and value[offset] not in '{}=;(),"':
                if value.startswith(("/*", "//"), offset):
                    break
                offset += 1
            if offset == start:
                raise ValueError("Unsupported project token")
            tokens.append(Token(value[start:offset], start, offset))
    return tokens


def project_settings(value):
    tokens = project_tokens(value)
    fields = {key: [] for key in KEYS}
    scopes, identities, next_scope = [(0, set())], {key: set() for key in KEYS}, 0
    dictionaries = {0: {"parent": None, "label": None, "fields": {}}}
    for index, token in enumerate(tokens):
        if not token.quoted and token.value == "{":
            next_scope += 1
            label = (tokens[index - 2].value if index >= 2
                     and tokens[index - 1].value == "=" else None)
            dictionaries[next_scope] = {
                "parent": scopes[-1][0], "label": label, "fields": {},
            }
            scopes.append((next_scope, set()))
        elif not token.quoted and token.value == "}":
            if len(scopes) == 1:
                raise ValueError("Unbalanced project dictionary")
            scopes.pop()
        elif index + 3 < len(tokens) and tokens[index + 1].value == "=" \
                and tokens[index + 3].value == ";" and token.value in ("isa", "name"):
            metadata = dictionaries[scopes[-1][0]]["fields"]
            if token.value in metadata:
                raise ValueError(f"Duplicate configuration metadata: {token.value}")
            metadata[token.value] = tokens[index + 2].value
        elif token.value in fields and index + 1 < len(tokens) \
                and tokens[index + 1].value == "=":
            if index + 3 >= len(tokens) or tokens[index + 3].value != ";" \
                    or (not tokens[index + 2].quoted
                        and tokens[index + 2].value in "{}=;(),"):
                raise ValueError(f"Malformed source assignment: {token.value}")
            scope, seen = scopes[-1]
            if token.value in seen:
                raise ValueError(f"Duplicate source assignment: {token.value}")
            seen.add(token.value)
            identities[token.value].add(scope)
            actual = tokens[index + 2]
            fields[token.value].append((actual.value, actual.start, actual.end))
    if len(scopes) != 1:
        raise ValueError("Unbalanced project dictionary")
    if any(len(values) != SETTING_COUNT for values in fields.values()) \
            or identities[KEYS[0]] != identities[KEYS[1]]:
        raise ValueError(f"Expected {SETTING_COUNT} coordinated source assignments for each version key")
    configurations = {}
    for scope in identities[KEYS[0]]:
        settings = dictionaries[scope]
        parent = dictionaries[settings["parent"]]
        identifier = parent["label"]
        if settings["label"] != "buildSettings" or identifier in configurations \
                or parent["fields"] != {
                    "isa": "XCBuildConfiguration", "name": CONFIGURATIONS.get(identifier),
                }:
            raise ValueError("Unexpected source configuration topology")
        configurations[identifier] = parent["fields"]["name"]
    if configurations != CONFIGURATIONS:
        raise ValueError("Source configuration names or identities have drifted")
    return fields


def resolve_settings(fields):
    resolved = {}

    def resolve(key, visiting=()):
        if key in visiting:
            raise ValueError("Cyclic source version reference")
        if key not in resolved:
            values = set()
            for value, _, _ in fields[key]:
                if value in REFERENCES:
                    value = resolve(REFERENCES[value], visiting + (key,))
                elif not re.fullmatch(VERSION, value):
                    raise ValueError(f"{key} must resolve to canonical X.Y.Z")
                values.add(value)
            if len(values) != 1:
                raise ValueError(f"{key} has drifted across configurations")
            resolved[key] = values.pop()
        return resolved[key]

    if len({resolve(key) for key in KEYS}) != 1:
        raise ValueError("Coordinated source versions have drifted")
    return resolved


def load_plist(data):
    try:
        fmt = plistlib.FMT_BINARY if data.startswith(b"bplist00") else plistlib.FMT_XML
        return plistlib.loads(data, fmt=fmt, dict_type=UniqueDictionary)
    except (plistlib.InvalidFileException, ExpatError, UnicodeError) as error:
        raise ValueError("Invalid plist serialization") from error


def localized_fields(value):
    fields, offset = UniqueDictionary(), 0
    while offset < len(value):
        token = LOCALIZED_TOKEN.match(value, offset)
        if token is None:
            raise ValueError("Unsupported strings serialization")
        if token["key"] is not None:
            fields[token["key"].strip('"')] = (
                token["value"], token.start("value"), token.end("value"),
            )
        offset = token.end()
    return fields


def validate_localized(value, version, resolved):
    fields = localized_fields(value)
    if SHORT not in fields or INFO not in fields:
        raise ValueError("Localized display metadata is missing")
    for key in (SHORT, BUILD):
        if key in fields:
            actual = fields[key][0]
            if resolved.get(REFERENCES.get(actual), actual) != version:
                raise ValueError("Localized display version is invalid or has drifted")
    if fields[INFO][0] != INFO_PREFIX + version + INFO_SUFFIX:
        raise ValueError("Localized information version is invalid or has drifted")
    return fields


def inspect_data(data):
    decoded = {path: decode(data[path])[0] for path in SOURCE_PATHS}
    settings = project_settings(decoded[PROJECT])
    resolved = resolve_settings(settings)
    version = resolved[KEYS[0]]
    plist = load_plist(data[PLIST])
    if not isinstance(plist, dict) or SHORT not in plist:
        raise ValueError("Source Info.plist requires a dictionary with short version")
    xml = re.sub(r"<!--.*?-->", lambda match: " " * len(match[0]),
                 decoded[PLIST], flags=re.DOTALL)
    serialized = list(PLIST_VERSION.finditer(xml))
    if len(serialized) != 1 + (BUILD in plist):
        raise ValueError("Source plist versions must be editable XML strings")
    edits = {path: [] for path in SOURCE_PATHS}
    for key in (SHORT, BUILD):
        if key not in plist:
            continue
        value = plist[key]
        if not isinstance(value, str) or resolved.get(REFERENCES.get(value), value) != version:
            raise ValueError(f"{key} is invalid, unresolved or has drifted")
        matches = [match for match in serialized if f"<key>{key}</key>" in match[1]]
        if len(matches) != 1 or matches[0][2] != value:
            raise ValueError(f"{key} has unsupported XML serialization")
        if value not in REFERENCES:
            edits[PLIST].append(matches[0].span(2))
    for values in settings.values():
        edits[PROJECT].extend((start, end) for value, start, end in values
                              if value not in REFERENCES)
    fields = validate_localized(decoded[LOCALIZED], version, resolved)
    for key in (SHORT, BUILD):
        if key in fields and fields[key][0] not in REFERENCES:
            edits[LOCALIZED].append(fields[key][1:])
    start = fields[INFO][1] + len(INFO_PREFIX)
    edits[LOCALIZED].append((start, start + len(version)))
    return version, edits


def inspect(root=Path(".")):
    root = Path(root)
    return inspect_data({path: (root / path).read_bytes() for path in SOURCE_PATHS})


def read(root=Path(".")):
    return inspect(root)[0]


def bump(mode, root=Path(".")):
    index = ("major", "minor", "patch").index(mode)
    root = Path(root)
    data = {path: (root / path).read_bytes() for path in SOURCE_PATHS}
    current, edits = inspect_data(data)
    parts = list(map(int, current.split(".")))
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    new = ".".join(map(str, parts))
    updates = {}
    for path in SOURCE_PATHS:
        value, encoding, bom = decode(data[path])
        for start, end in sorted(edits[path], reverse=True):
            value = value[:start] + new + value[end:]
        updates[path] = bom + value.encode(encoding)
    if inspect_data(updates)[0] != new:
        raise ValueError("Rendered metadata did not validate")
    for path, content in updates.items():
        if content != data[path]:
            (root / path).write_bytes(content)
    return new


def built_dictionary(path):
    raw = Path(path).read_bytes()
    if raw.startswith(b"bplist00") or decode(raw)[0].lstrip().startswith("<"):
        value = load_plist(raw)
        if not isinstance(value, dict):
            raise ValueError("Built metadata must be a dictionary")
        return value
    return {key: field[0] for key, field in localized_fields(decode(raw)[0]).items()}


def verify_bundle(bundle, root=Path(".")):
    root, bundle = Path(root), Path(bundle)
    version = read(root)
    source = load_plist((root / PLIST).read_bytes())
    actual = load_plist((bundle / "Contents/Info.plist").read_bytes())
    if not isinstance(actual, dict):
        raise ValueError("Built Info.plist must be an XML/binary plist dictionary")
    for key in (SHORT, BUILD):
        if (key in source) != (key in actual) or (key in source and actual[key] != version):
            raise ValueError("Built bundle version differs from selected source")
    localized = localized_fields(text(root / LOCALIZED))
    actual = built_dictionary(bundle / "Contents/Resources" / LOCALIZED)
    expected = {
        key: version if key in (SHORT, BUILD) and field[0] in REFERENCES else field[0]
        for key, field in localized.items()
    }
    if actual != expected:
        raise ValueError("Built localized display differs from complete selected source")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--bump", choices=("patch", "minor", "major"))
    operation.add_argument("--verify-bundle", type=Path)
    args = parser.parse_args()
    if args.verify_bundle:
        verify_bundle(args.verify_bundle)
    else:
        print(bump(args.bump) if args.bump else read())
