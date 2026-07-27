from __future__ import annotations

import re
import subprocess
import sys

SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
    r"\s*[:=]\s*[\"']?([A-Za-z0-9._~+/=-]{12,})"
)
SKIP_PREFIXES = ("tests/", "docs/")
SKIP_FILES = {
    "src/tutor_assistant/security/redaction.py",
    "scripts/scan_added_secrets.py",
}


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    diff = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}...HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    path = ""
    findings: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if path.startswith(SKIP_PREFIXES) or path in SKIP_FILES:
            continue
        value = line[1:]
        for match in SECRET.finditer(value):
            candidate = match.group(1)
            if any(marker in candidate for marker in ("{", "$", "<", "REDACTED")):
                continue
            findings.append(f"{path}: possible secret literal")
    if findings:
        print("\n".join(findings))
        return 1
    print("No added secret literals detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
