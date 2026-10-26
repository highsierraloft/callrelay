"""Generate packaged fallback WAV files once, never during a live call.

Uses the existing Inworld account. Pass a private key-file path, never a key argument.
API: https://docs.inworld.ai/api-reference/ttsAPI/texttospeech/synthesize-speech
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import wave
from pathlib import Path

import httpx

from app.reception_audio import EXIT_MESSAGES


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    key = args.key_file.read_text().strip()
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=40, follow_redirects=False) as client:
        for kind, translations in EXIT_MESSAGES.items():
            for language, text in translations.items():
                target = args.output / f"reception-{kind}-{language}.wav"
                if target.exists():
                    continue
                response = client.post(
                    "https://api.inworld.ai/tts/v1/voice",
                    headers={"Authorization": "Basic " + key},
                    json={
                        "text": text,
                        "voiceId": "Wade",
                        "modelId": "inworld-tts-2",
                        "deliveryMode": "STABLE",
                        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 8000},
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Fallback speech generation failed: HTTP {response.status_code}"
                    )
                data = base64.b64decode(response.json()["audioContent"], validate=True)
                with wave.open(io.BytesIO(data)) as audio:
                    assert (
                        audio.getframerate() == 8000
                        and audio.getsampwidth() == 2
                        and audio.getnchannels() == 1
                    )
                    assert 0 < audio.getnframes() / 8000 <= 10
                target.write_bytes(data)
                print(json.dumps({"file": target.name, "bytes": len(data)}))


if __name__ == "__main__":
    main()
