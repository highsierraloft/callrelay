"""Generate the public fallback prompts locally, without provider keys or call audio."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import wave
from pathlib import Path

from app.reception_audio import EXIT_MESSAGES


def generate(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    files = {}
    with tempfile.TemporaryDirectory(prefix="callrelay-audio-") as temporary:
        intermediate = Path(temporary) / "speech.wav"
        for kind, translations in EXIT_MESSAGES.items():
            for language, text in translations.items():
                name = f"reception-{kind}-{language}.wav"
                output = destination / name
                subprocess.run(
                    ["espeak-ng", "-v", language, "-s", "170", "-w", str(intermediate), text],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "ffmpeg",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(intermediate),
                        "-map_metadata",
                        "-1",
                        "-fflags",
                        "+bitexact",
                        "-flags:a",
                        "+bitexact",
                        "-ar",
                        "8000",
                        "-ac",
                        "1",
                        "-c:a",
                        "pcm_s16le",
                        str(output),
                    ],
                    check=True,
                )
                with wave.open(str(output)) as audio:
                    duration = audio.getnframes() / audio.getframerate()
                files[name] = {
                    "text": text,
                    "language": language,
                    "seconds": round(duration, 3),
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                }
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "generator": "eSpeak NG",
                "source": "app/reception_audio.py",
                "purpose": "Synthetic service prompts, not call recordings",
                "format": "mono PCM16 WAV at 8000 Hz",
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("deploy/asterisk/sounds/reception"))
    generate(parser.parse_args().output)
