"""CSS-15 disposable plain/age experiment; dummy data, not a backup format or API."""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

RUNNER = Path(__file__).with_name("backup_age_probe.exp")
SIZE = 64 * 1024 * 1024
SETTINGS_SIZE = 1024 * 1024
BLOCK = b"css15-synthetic-data" * 32768
MAX_LOG_N = 18  # Experimental restore limit, not approved policy.


class SyntheticInput(io.RawIOBase):
    def __init__(self, size):
        self.size = size
        self.remaining = size
        self.digest = hashlib.sha256()

    def readable(self):
        return True

    def read(self, size=-1):
        if not self.remaining:
            return b""
        offset = (self.size - self.remaining) % len(BLOCK)
        data = BLOCK[
            offset : offset
            + min(self.remaining, len(BLOCK) - offset, size if size >= 0 else len(BLOCK))
        ]
        self.remaining -= len(data)
        self.digest.update(data)
        return data


def checked_header(path):
    """Reject expensive or unexpected scrypt stanzas before invoking age."""
    with path.open("rb") as source:
        lines = [source.readline(256) for _ in range(4)]
    if lines[0] != b"age-encryption.org/v1\n":
        raise ValueError("unsupported age header")
    parts = lines[1].split()
    if len(parts) != 4 or parts[:2] != [b"->", b"scrypt"]:
        raise ValueError("unsupported recipient")
    factor = parts[3].rstrip(b"\n")
    if not factor.isascii() or not factor.isdigit() or factor.startswith(b"0"):
        raise ValueError("invalid work factor")
    if int(factor) > MAX_LOG_N:
        raise ValueError("scrypt work factor exceeds experimental limit")


def archive(destination, source, kind):
    try:
        with tarfile.open(destination, mode="w|") as output:
            expected = hashlib.sha256()
            for _ in range(source.size // len(BLOCK)):
                expected.update(BLOCK)
            expected.update(BLOCK[: source.size % len(BLOCK)])
            manifest = json.dumps(
                {"kind": kind, "size": source.size, "sha256": expected.hexdigest()}
            ).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest)
            output.addfile(info, io.BytesIO(manifest))
            info = tarfile.TarInfo("dummy.bin")
            info.size = source.size
            output.addfile(info, source)
    except BrokenPipeError:
        pass


def decrypt_checked(ciphertext, destination, action="decrypt"):
    """Remove partial plaintext if age reports a damaged or unauthenticated file."""
    checked_header(ciphertext)
    try:
        result = subprocess.run(
            ["expect", str(RUNNER), action, str(ciphertext), str(destination)],
            timeout=45,
            check=False,
        )
        if result.returncode:
            raise ValueError("age rejected ciphertext")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def check_archive(path, kind, size):
    """Bound the disposable manifest and members before accepting their checksums."""
    with tarfile.open(path, mode="r|") as stream:
        manifest_member = stream.next()
        if (
            manifest_member is None
            or manifest_member.name != "manifest.json"
            or not manifest_member.isfile()
            or manifest_member.size > 4096
        ):
            raise ValueError("invalid manifest member")
        with stream.extractfile(manifest_member) as content:
            manifest = json.load(content)
        if manifest.get("kind") != kind or manifest.get("size") != size:
            raise ValueError("invalid manifest values")
        member = stream.next()
        if (
            member is None
            or member.name != "dummy.bin"
            or not member.isfile()
            or member.size != size
        ):
            raise ValueError("invalid data member")
        digest = hashlib.sha256()
        with stream.extractfile(member) as content:
            for chunk in iter(lambda: content.read(1024 * 1024), b""):
                digest.update(chunk)
        if stream.next() is not None or digest.hexdigest() != manifest.get("sha256"):
            raise ValueError("archive checksum or member count mismatch")
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="CSS-15 disposable container evaluation")
    parser.add_argument(
        "--plain", action="store_true", help="evaluate the plain archive without age"
    )
    parser.add_argument("--kind", choices=("full", "settings"), default="full")
    args = parser.parse_args()
    size = SIZE if args.kind == "full" else SETTINGS_SIZE
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="css15-age-") as temp:
        root = Path(temp)
        if args.plain:
            source = SyntheticInput(size)
            plain = root / "backup.tar"
            archive(plain, source, args.kind)
            assert check_archive(plain, args.kind, size) == source.digest.hexdigest()
            print(
                f"plain {args.kind} {size} bytes in {time.monotonic() - started:.2f}s; "
                f"archive {plain.stat().st_size} bytes"
            )
            for name in ("tampered", "truncated"):
                suspect = root / name
                shutil.copyfile(plain, suspect)
                with suspect.open("r+b") as output:
                    if name == "tampered":
                        output.seek(4096)
                        output.write(bytes([output.read(1)[0] ^ 1]))
                    else:
                        output.truncate(suspect.stat().st_size // 2)
                try:
                    check_archive(suspect, args.kind, size)
                except (ValueError, tarfile.TarError, OSError):
                    print(f"plain {name}: rejected")
                else:
                    raise AssertionError(f"plain {name}: unexpectedly accepted")
            unsafe = root / "unsafe.tar"
            with tarfile.open(unsafe, mode="w|") as output:
                info = tarfile.TarInfo("manifest.json")
                info.size = 4097
                output.addfile(info, io.BytesIO(b"x" * info.size))
            try:
                check_archive(unsafe, args.kind, size)
            except ValueError:
                print("plain oversized manifest: rejected")
            else:
                raise AssertionError("plain oversized manifest: unexpectedly accepted")
            return
        fifo = root / "stream"
        os.mkfifo(fifo, 0o600)
        source = SyntheticInput(size)
        writer = threading.Thread(target=archive, args=(fifo, source, args.kind))
        writer.start()
        try:
            subprocess.run(
                ["expect", str(RUNNER), "encrypt", str(fifo), str(root / "backup.age")],
                timeout=45,
                check=True,
            )
        finally:
            writer.join(timeout=5)
        ciphertext = root / "backup.age"
        checked_header(ciphertext)
        print(
            f"encrypted {args.kind} {size} bytes in {time.monotonic() - started:.2f}s; "
            f"ciphertext {ciphertext.stat().st_size} bytes"
        )
        plain = root / "staged.tar"
        decrypt_checked(ciphertext, plain)
        assert check_archive(plain, args.kind, size) == source.digest.hexdigest()
        plain.unlink()
        for name in ("wrong-password", "tampered", "truncated", "excessive-kdf"):
            suspect = root / name
            if name != "wrong-password":
                shutil.copyfile(ciphertext, suspect)
                with suspect.open("r+b") as output:
                    if name == "tampered":
                        output.seek(-32, os.SEEK_END)
                        output.write(bytes([output.read(1)[0] ^ 1]))
                    elif name == "truncated":
                        output.truncate(suspect.stat().st_size - 40)
                    else:
                        header = output.read(150)
                        assert b" 18\n" in header
                        output.seek(0)
                        output.write(header.replace(b" 18\n", b" 30\n", 1))
            try:
                decrypt_checked(
                    ciphertext if name == "wrong-password" else suspect,
                    plain,
                    "decrypt-wrong" if name == "wrong-password" else "decrypt",
                )
            except ValueError:
                assert not plain.exists(), name
                print(f"{name}: rejected; partial plaintext removed")
            else:
                raise AssertionError(f"{name}: unexpectedly accepted")
        print(f"round trip and negative checks: {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
