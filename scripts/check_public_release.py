"""Check tracked release files without reading ignored operator data."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

FORBIDDEN_PARTS = {
    ".release",
    "private",
    ".venv",
    "node_modules",
    "__pycache__",
    "test-results",
    "playwright-report",
    "recordings",
    ".gradle",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".mp3",
    ".m4a",
    ".log",
    ".apk",
    ".aab",
    ".jks",
    ".keystore",
    ".pem",
    ".key",
    ".p12",
    ".pyc",
    ".zip",
}
TEXT_RULES = {
    "personal Mac path": re.compile(r"/Users/[A-Za-z0-9_.-]+/"),
    "personal Linux path": re.compile(
        r"/home/(?!phone/|agent/|asterisk/|app/|user/)[A-Za-z0-9_.-]+/"
    ),
    "specific tailnet": re.compile(r"[A-Za-z0-9.-]+\.tail[a-f0-9]{5,}\.ts\.net"),
    "provider credential": re.compile(r"sk-or-v1-[a-f0-9]{40,}|snx_proj_[A-Za-z0-9_.-]{40,}"),
    "GitHub credential": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def inspect(root: Path, paths: list[str]) -> list[str]:
    findings = []
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            findings.append("Unsafe tracked path")
            continue
        if (
            any(part in FORBIDDEN_PARTS for part in relative.parts)
            or relative.suffix.lower() in FORBIDDEN_SUFFIXES
        ):
            findings.append(f"{name}: runtime or private artifact")
            continue
        if relative.name.startswith(".env") and relative.name != ".env.example":
            findings.append(f"{name}: environment values")
        path = root / relative
        if path.is_symlink():
            findings.append(f"{name}: symlink needs review")
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            allowed = (
                relative == Path("phone/gradle/wrapper/gradle-wrapper.jar")
                or (
                    relative.parts[:2] == ("docs", "images") and relative.suffix in {".png", ".jpg"}
                )
                or (
                    relative.parent == Path("deploy/asterisk/sounds/reception")
                    and relative.suffix == ".wav"
                )
            )
            if not allowed:
                findings.append(f"{name}: unreviewed binary")
            continue
        for label, pattern in TEXT_RULES.items():
            if pattern.search(text):
                findings.append(f"{name}: {label}")
    return findings


def check_audio(root: Path) -> list[str]:
    from app.reception_audio import EXIT_MESSAGES

    folder = root / "deploy/asterisk/sounds/reception"
    manifest = json.loads((folder / "manifest.json").read_text())
    expected = {
        f"reception-{kind}-{language}.wav": text
        for kind, translations in EXIT_MESSAGES.items()
        for language, text in translations.items()
    }
    findings = []
    if manifest.get("generator") != "eSpeak NG" or set(manifest["files"]) != set(expected):
        findings.append("Fallback audio provenance is incomplete")
    for name, text in expected.items():
        info = manifest["files"].get(name, {})
        if (
            info.get("text") != text
            or info.get("sha256") != hashlib.sha256((folder / name).read_bytes()).hexdigest()
        ):
            findings.append(f"{name}: fallback source or hash differs")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=args.root).decode().split("\0")
    paths = [p for p in paths if p]
    if not paths:
        raise SystemExit(
            "No tracked files. Stage the intended source before the publication check."
        )
    findings = inspect(args.root, paths) + check_audio(args.root)
    wrapper = args.root / "phone/gradle/wrapper/gradle-wrapper.jar"
    if (
        hashlib.sha256(wrapper.read_bytes()).hexdigest()
        != "497c8c2a7e5031f6aa847f88104aa80a93532ec32ee17bdb8d1d2f67a194a9c7"
    ):
        findings.append("Gradle wrapper differs from the reviewed official 9.6.0 wrapper")
    if findings:
        print("\n".join(findings))
        raise SystemExit(1)
    print(
        f"Public-source checks passed for {len(paths)} tracked files. Run Gitleaks as a separate check."
    )


if __name__ == "__main__":
    main()
