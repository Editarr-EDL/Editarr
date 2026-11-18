"""Stage pinned media inputs and assemble a verified Windows or Linux release artifact."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from scripts.release_assets import source_identity, validate_release_versions

BOOTSTRAP_RELEASE = "https://github.com/Editarr-EDL/Editarr/releases/download/v0.1.2"
BOOTSTRAP_HASHES = {
    "Third-Party-Source": "bdf3a2abbbebf2c6c14bba3c4988bc3428ac10f375dcc908c9e002fab35b7d3b",
    "Notices": "3d6fd17785abdcacf0bacaad754b9a142730d231bfb9268a9760d29167a385fc",
}
MEDIA_FILES = {
    "MEDIA-SHA256SUMS.txt",
    "av-18.1.0.tar.gz",
    "ffmpeg-8.1.3.tar.xz",
    "lame-4.0.tar.gz",
    "opencv-optional-typing.patch",
    "opencv_python_headless-5.0.0.93.tar.gz",
    "pkgconf-2.5.1.tar.xz",
    "x264-b35605a.tar.gz",
}
COMMON_SOURCE_FILES = {
    "Python-3.11.16.tar.xz",
    "certifi-2026.7.22.tar.gz",
    "glin_profanity-3.4.0.tar.gz",
    "pyinstaller-6.22.2.tar.gz",
    "scipy-1.17.1-src.tar.gz",
    "sentencepiece-0.2.2.tar.gz",
    "tokenizers-0.22.2.tar.gz",
    "tqdm-4.70.0.tar.gz",
}
TARGETS = {
    "windows-x64": ("windows", "Windows", "x64-Setup.exe"),
    "linux-x86_64": ("linux", "Linux", "x86_64.AppImage"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_bootstrap(archive_kind: str, destination: Path, selected: set[str]) -> None:
    """Unpack the selected files from the checksum-pinned public bootstrap archive."""
    filename = f"Editarr-v0.1.2-macOS-{archive_kind}.tar.gz"
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        archive = Path(temp) / filename
        local_directory = os.environ.get("EDITARR_RELEASE_BOOTSTRAP_DIR")
        if local_directory:
            shutil.copyfile(Path(local_directory) / filename, archive)
        else:
            subprocess.run(
                ["curl", "--fail", "--location", "--retry", "3", "--silent", "--show-error",
                 "--output", str(archive), f"{BOOTSTRAP_RELEASE}/{filename}"],
                check=True,
            )
        if sha256(archive) != BOOTSTRAP_HASHES[archive_kind]:
            raise ValueError(f"Unexpected SHA-256 digest for {filename}")
        with tarfile.open(archive, "r:gz") as contents:
            members = (
                member for member in contents
                if PurePosixPath(member.name).parts
                and PurePosixPath(member.name).parts[0] in selected
            )
            contents.extractall(destination, members, filter="data")


def collect_notices(bootstrap: Path) -> None:
    """Collect the license files installed by the current Python and Go environments."""
    notices = bootstrap / "notices"
    for distribution in importlib.metadata.distributions():
        package = distribution.metadata["Name"]
        if not package:
            continue
        for relative in distribution.files or ():
            if relative.is_absolute() or ".." in relative.parts:
                continue
            if ("licenses" not in relative.parts and not relative.name.upper().startswith(
                ("LICENSE", "COPYING", "COPYRIGHT", "NOTICE")
            )):
                continue
            path = Path(distribution.locate_file(relative))
            if path.is_file():
                output = notices / "python" / package / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, output)
    python_home = Path(sys.base_prefix)
    for license_file in (python_home / "LICENSE.txt", python_home / "lib/python3.11/LICENSE.txt"):
        if license_file.is_file():
            shutil.copyfile(license_file, notices / "CPython-LICENSE.txt")
            break
    if shutil.which("go"):
        go_root = Path(subprocess.check_output(["go", "env", "GOROOT"], text=True).strip())
        for license_file in (go_root / "LICENSE", go_root.parent / "LICENSE"):
            if license_file.is_file():
                shutil.copyfile(license_file, notices / "go/Go-runtime/Go-LICENSE")
                break


def prepare(root: Path, target: str, bootstrap: Path) -> None:
    """Fetch the target's inputs and create its notices and rebuild instructions."""
    platform, label, _ = TARGETS[target]
    version = validate_release_versions(root)
    repo = os.environ.get("GITHUB_REPOSITORY", "Editarr-EDL/Editarr")
    release_url = f"https://github.com/{repo}/releases/tag/v{version}"
    source_url = f"https://github.com/{repo}/tree/v{version}"
    sources, notices = bootstrap / "sources", bootstrap / "notices"
    sources.mkdir(parents=True)
    notices.mkdir()
    extract_bootstrap("Third-Party-Source", sources, MEDIA_FILES | COMMON_SOURCE_FILES)
    extract_bootstrap("Notices", notices, {"media", "go", "web"})
    for line in (sources / "MEDIA-SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, filename = line.split()
        if filename not in MEDIA_FILES or sha256(sources / filename) != digest:
            raise ValueError(f"Unexpected media input {filename}")
    shutil.copytree(root / "native_build" / platform, sources / "native_build" / platform)
    shutil.copyfile(root / "LICENSE", notices / "Editarr-GPLv3.txt")
    (notices / "THIRD-PARTY-NOTICES.md").write_text(
        f"# Editarr v{version} for {label}\n\n"
        "Editarr is distributed under the GNU General Public License v3.0. "
        f"The application source and lockfiles are at {source_url}.\n\n"
        "FFmpeg and ffprobe use the FFmpeg, x264 and LAME sources shipped with the release. "
        "PyAV uses those FFmpeg libraries. The OpenCV source archive and packaging patch "
        "are included with the media source inputs. The corresponding source archive "
        f"Editarr-v{version}-{label}-Third-Party-Source.tar.gz is available at {release_url}.\n\n"
        "See the license files for their respective components. "
        "See docs/model-artifact-notices.md in the tagged source for model license metadata.\n",
        encoding="utf-8",
    )
    (sources / "SOURCE-README.txt").write_text(
        f"Editarr v{version} for {label}\n\n"
        "This archive contains the pinned FFmpeg, x264, LAME, PyAV and OpenCV inputs "
        f"and the native_build/{platform} recipes. Verify the media inputs against "
        "MEDIA-SHA256SUMS.txt. See the build workflow, dependency lockfiles and "
        f"application source at {source_url}.\n",
        encoding="utf-8",
    )


def package(root: Path, target: str, bootstrap: Path, installer: Path, output: Path) -> None:
    """Create the three downloadable files and provenance for the release publisher."""
    _, label, installer_suffix = TARGETS[target]
    version = validate_release_versions(root)
    revision, dirty = source_identity(root)
    if revision is None or dirty is not False:
        raise ValueError("Alpha release packaging requires a clean Git checkout")
    if not installer.is_file() or installer.stat().st_size == 0:
        raise ValueError(f"Missing installer payload {installer}")
    output.mkdir(parents=True, exist_ok=True)
    collect_notices(bootstrap)

    base = f"Editarr-v{version}-{label}"
    binary = output / f"{base}-{installer_suffix}"
    shutil.copy2(installer, binary)
    files = [binary]
    for kind, directory in (("Notices", "notices"), ("Third-Party-Source", "sources")):
        archive = output / f"{base}-{kind}.tar.gz"
        with tarfile.open(archive, "w:gz") as contents:
            contents.add(bootstrap / directory, arcname=".")
        files.append(archive)
    (output / "artifact-verification.json").write_text(
        json.dumps(
            {"version": version, "source_revision": revision, "target": target,
             "sha256": {file.name: sha256(file) for file in files}},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_command = commands.add_parser("prepare")
    package_command = commands.add_parser("package")
    for command in (prepare_command, package_command):
        command.add_argument("target", choices=TARGETS)
        command.add_argument("bootstrap", type=Path)
    package_command.add_argument("installer", type=Path)
    package_command.add_argument("output", type=Path)
    collect_command = commands.add_parser("collect-notices")
    collect_command.add_argument("bootstrap", type=Path)
    args = parser.parse_args()

    if args.command == "prepare":
        prepare(Path.cwd(), args.target, args.bootstrap)
    elif args.command == "package":
        package(Path.cwd(), args.target, args.bootstrap, args.installer, args.output)
    else:
        collect_notices(args.bootstrap)


if __name__ == "__main__":
    main()
