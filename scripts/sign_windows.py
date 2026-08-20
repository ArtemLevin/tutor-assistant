"""Sign Windows binaries without storing certificates or passwords in the checkout."""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def sign_and_verify(paths: list[Path], *, allow_unsigned: bool = False) -> bool:
    certificate = os.environ.get("WINDOWS_SIGNING_CERTIFICATE", "")
    password = os.environ.get("WINDOWS_SIGNING_PASSWORD", "")
    if not certificate:
        if allow_unsigned:
            return False
        raise RuntimeError("A signing certificate is required; configure WINDOWS_SIGNING_CERTIFICATE")
    signtool = shutil.which("signtool.exe") or shutil.which("signtool")
    if signtool is None:
        raise RuntimeError("Windows signtool.exe is required to sign release artifacts")
    handle = tempfile.NamedTemporaryFile(prefix="tutor-assistant-signing-", suffix=".pfx", delete=False)
    certificate_path = Path(handle.name)
    try:
        handle.write(base64.b64decode(certificate, validate=True))
        handle.close()
        for artifact in paths:
            try:
                subprocess.run(
                    [
                        signtool,
                        "sign",
                        "/f",
                        str(certificate_path),
                        "/p",
                        password,
                        "/fd",
                        "SHA256",
                        "/tr",
                        "http://timestamp.digicert.com",
                        "/td",
                        "SHA256",
                        str(artifact.resolve()),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [signtool, "verify", "/pa", str(artifact.resolve())],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError:
                message = f"Windows signing or signature verification failed for {artifact.name}"
                raise RuntimeError(message) from None
    finally:
        handle.close()
        certificate_path.unlink(missing_ok=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--allow-unsigned", action="store_true")
    args = parser.parse_args()
    signed = sign_and_verify(args.artifacts, allow_unsigned=args.allow_unsigned)
    print("signed-and-verified" if signed else "unsigned-explicit-exception")


if __name__ == "__main__":
    main()
