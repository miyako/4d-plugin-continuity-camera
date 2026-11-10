"""Validate all physical cleanup paths before deleting only proven Default outputs."""

import argparse
import errno
import os
from pathlib import Path
import re
import shutil
import stat
import sys

sys.dont_write_bytecode = True
import repository


PRODUCT = "Continuity Camera"
BASE = Path("test/Plugins")
BUNDLE = BASE / (PRODUCT + ".bundle")
FILES = tuple(BUNDLE / relative for relative in (
    f"Contents/MacOS/{PRODUCT}", "Contents/Info.plist",
    "Contents/Resources/English.lproj/InfoPlist.strings",
    "Contents/Resources/constants.xlf", "Contents/Resources/manifest.json",
    "Contents/_CodeSignature/CodeResources",
))
PRESERVED = tuple(BUNDLE / relative for relative in (
    f"Contents/Windows/{PRODUCT}.4DX", f"Contents/Windows64/{PRODUCT}.4DX",
    "Contents/QuickLook/Thumbnail.png",
))
XCODE_DATA = BASE / "XCBuildData"
DIRECTORIES = (BASE / (PRODUCT + ".build"),)


def generated(path):
    return (path in set(map(str, FILES))
            or bool(re.fullmatch(
                r"test/Plugins/XCBuildData/(?:build\.db(?:-wal|-shm)?|"
                r"[0-9a-f]{32}\.xcbuilddata/.+)", path))
            or any(path.startswith(str(directory) + "/") for directory in DIRECTORIES))


def optional_stat(path):
    try:
        return path.lstat()
    except OSError as error:
        if error.errno != errno.ENOENT:
            raise
        return None


def access(path, mode, permissions):
    bits = {os.R_OK: 0o444, os.W_OK: 0o222, os.X_OK: 0o111}
    if not all(mode & bits[permission] for permission in permissions) or not os.access(
            path, sum(permissions)):
        raise ValueError(f"Inaccessible output path: {path}")


def directory(path, info):
    mode = info.st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError(f"Expected nonsymlink directory: {path}")
    access(path, mode, (os.R_OK, os.X_OK))
    if path != Path(".") and optional_stat(path / ".git") is not None:
        raise ValueError(f"Nested Git marker: {path / '.git'}")


def inspect_node(path, *, descend=True):
    info = optional_stat(path)
    if info is None:
        return None
    mode = info.st_mode
    if stat.S_ISLNK(mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Refusing symlink/reparse output: {path}")
    if stat.S_ISDIR(mode):
        directory(path, info)
        if descend:
            for child in sorted(path.iterdir()):
                inspect_node(child)
    elif stat.S_ISREG(mode):
        access(path, mode, (os.R_OK,))
    else:
        raise ValueError(f"Nonregular output: {path}")
    return info


def validate_paths(paths):
    for path in map(Path, paths):
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Output escapes checkout: {path}")
        directory(Path("."), Path(".").lstat())
        for parent in reversed(path.parents):
            if parent == Path("."):
                continue
            info = optional_stat(parent)
            if info is None:
                break
            directory(parent, info)
        inspect_node(path)


def prepare():
    validate_paths((*FILES, *DIRECTORIES, XCODE_DATA))
    for path in FILES:
        info = optional_stat(path)
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Cleanup leaf must be a regular file: {path}")
    for path in DIRECTORIES:
        info = optional_stat(path)
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Cleanup intermediate must be a directory: {path}")
    for path in (*FILES, *DIRECTORIES):
        if optional_stat(path) is None:
            continue
        parent = path.parent
        access(parent, parent.lstat().st_mode, (os.W_OK, os.X_OK))
        if path in DIRECTORIES:
            for node in (path, *sorted(path.rglob("*"))):
                if stat.S_ISDIR(node.lstat().st_mode):
                    access(node, node.lstat().st_mode, (os.W_OK, os.X_OK))
    if optional_stat(Path(".git")) is not None:
        tracked = set(repository.entries(repository.run(
            "git", "ls-tree", "-r", "-z", "HEAD", "--")))
        tracked.update(repository.entries(repository.run(
            "git", "ls-files", "--stage", "-z", "--"), index=True))
        if any(any(name == str(folder) or name.startswith(str(folder) + "/")
                   for folder in DIRECTORIES) for name in tracked):
            raise ValueError("Refusing to delete selected/indexed intermediate input")
    for path in FILES:
        if optional_stat(path) is not None:
            path.unlink()
    for path in DIRECTORIES:
        if optional_stat(path) is not None:
            shutil.rmtree(path)


def require_file(path):
    validate_paths((path,))
    info = optional_stat(path)
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise ValueError(f"Missing or empty regular native output: {path}")


def verify_resources():
    for name in ("manifest.json", "constants.xlf"):
        copied = BUNDLE / "Contents/Resources" / name
        require_file(copied)
        if copied.read_bytes() != Path(name).read_bytes():
            raise ValueError(f"Copied resource differs from source: {copied}")


def validate_unsigned():
    for path in (*FILES[:-1], *PRESERVED):
        require_file(path)
    verify_resources()


def validate_final():
    validate_unsigned()
    require_file(FILES[-1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("clean-macos", "verify-resources", "verify-final"))
    phase = parser.parse_args().phase
    if phase == "clean-macos":
        prepare()
    elif phase == "verify-final":
        validate_final()
    else:
        validate_unsigned()
