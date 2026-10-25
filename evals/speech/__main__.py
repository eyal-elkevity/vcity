import asyncio
import re
import sys
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict

from vcity.settings import VoiceSettings
from vcity.voice.speech import speakable

HERE = Path(__file__).parent
SAMPLE_RATE = 16000


class Phrase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    heard: tuple[str, ...]


def plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.casefold().replace("-", " ")).split())


def missing(phrase: Phrase, transcript: str) -> list[str]:
    said = plain(transcript)
    return [
        expected
        for expected in phrase.heard
        if not any(plain(option) in said for option in expected.split("|"))
    ]


async def round_trip(http: httpx.AsyncClient, voice: VoiceSettings, text: str) -> str:
    audio = await http.post(
        "https://api.deepgram.com/v1/speak",
        params={
            "model": voice.voice_model,
            "encoding": "linear16",
            "sample_rate": SAMPLE_RATE,
            "container": "none",
        },
        headers={"Authorization": f"Token {voice.deepgram_api_key.get_secret_value()}"},
        json={"text": speakable(text)},
    )
    audio.raise_for_status()
    heard = await http.post(
        "https://api.deepgram.com/v1/listen",
        params={"model": "nova-3", "encoding": "linear16", "sample_rate": SAMPLE_RATE},
        headers={"Authorization": f"Token {voice.deepgram_api_key.get_secret_value()}"},
        content=audio.content,
    )
    heard.raise_for_status()
    return str(heard.json()["results"]["channels"][0]["alternatives"][0]["transcript"])


async def run(phrases: list[Phrase]) -> list[str]:
    voice = VoiceSettings.from_env()
    lines: list[str] = []
    async with httpx.AsyncClient(timeout=30) as http:
        for phrase in phrases:
            transcript = await round_trip(http, voice, phrase.text)
            gaps = missing(phrase, transcript)
            lines.append(f"{'PASS' if not gaps else 'FAIL'} {phrase.text}")
            lines += [f"  heard: {transcript}", *(f"  missing: {gap}" for gap in gaps)]
    return lines


def main() -> None:
    raw = yaml.safe_load((HERE / "phrases.yaml").read_text("utf-8"))
    lines = asyncio.run(run([Phrase.model_validate(item) for item in raw]))
    sys.stdout.write("\n".join(lines) + "\n")
    sys.exit(1 if any(line.startswith("FAIL") for line in lines) else 0)


if __name__ == "__main__":
    main()
