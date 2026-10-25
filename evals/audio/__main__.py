import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.audio.client import Heard, call
from evals.audio.model import FIRST_AUDIO_CEILING, HERE, AudioCase, load_cases


def problems(case: AudioCase, heard: Heard) -> list[str]:
    spoken = heard.spoken.casefold()
    found = [
        f"never said {mention!r}"
        for mention in case.expect.mentions
        if not any(option.casefold() in spoken for option in mention.split("|"))
    ]
    if case.expect.transfer is not None and case.expect.transfer.value not in heard.transfers:
        found.append(f"no transfer to {case.expect.transfer}")
    if heard.first_audio_seconds is None:
        found.append("the agent never spoke")
    elif heard.first_audio_seconds > FIRST_AUDIO_CEILING:
        found.append(f"first audio after {heard.first_audio_seconds:.2f}s")
    return found


async def run(url: str, cases: Sequence[AudioCase]) -> list[str]:
    lines: list[str] = []
    for case in cases:
        heard = await call(url, HERE / case.wav)
        found = problems(case, heard)
        latency = f"{heard.first_audio_seconds:.2f}s" if heard.first_audio_seconds else "-"
        lines.append(f"{'PASS' if not found else 'FAIL'} {case.id}: first audio {latency}")
        lines += [f"  heard: {' '.join(heard.caller_transcript)}", f"  said: {heard.spoken}"]
        lines += [f"  problem: {p}" for p in found]
    return lines


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Play recorded callers into the voice line.")
    parser.add_argument("--url", default="ws://localhost:8080/ws/browser")
    args = parser.parse_args(argv)
    lines = asyncio.run(run(args.url, load_cases()))
    report = "\n".join(lines)
    sys.stdout.write(report + "\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as out:
            out.write(f"```\n{report}\n```\n")
    sys.exit(1 if any(line.startswith("FAIL") for line in lines) else 0)


if __name__ == "__main__":
    main()
