import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from evals.audio.client import Heard, call, ticketed_url
from evals.audio.model import FIRST_AUDIO_CEILING, HERE, AudioCase, load_cases
from vcity.voice.logs import configure_logging


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
    if heard.interruptions:
        found.append(f"the agent was cut off {heard.interruptions} time(s)")
    if heard.ended is not None:
        found.append(f"call ended early: {heard.ended}")
    return found


LINE_BUSY = 409
LINE_RETRIES = 10


async def ticket_once_the_line_is_free(base: str, code: str) -> str:
    for _ in range(LINE_RETRIES - 1):
        try:
            return await ticketed_url(base, code)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != LINE_BUSY:
                raise
            await asyncio.sleep(1)
    return await ticketed_url(base, code)


async def run(base: str, code: str, cases: Sequence[AudioCase]) -> list[str]:
    lines: list[str] = []
    for case in cases:
        aside = (HERE / case.aside.wav, case.aside.after) if case.aside else None
        heard = await call(await ticket_once_the_line_is_free(base, code), HERE / case.wav, aside)
        found = problems(case, heard)
        latency = f"{heard.first_audio_seconds:.2f}s" if heard.first_audio_seconds else "-"
        lines.append(f"{'PASS' if not found else 'FAIL'} {case.id}: first audio {latency}")
        lines += [f"  heard: {' '.join(heard.caller_transcript)}", f"  said: {heard.spoken}"]
        lines += [f"  problem: {p}" for p in found]
    return lines


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Play recorded callers into the voice line.")
    parser.add_argument("--base", default="http://localhost:8080")
    args = parser.parse_args(argv)
    configure_logging()
    lines = asyncio.run(run(args.base, os.environ.get("ACCESS_CODE", ""), load_cases()))
    report = "\n".join(lines)
    sys.stdout.write(report + "\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as out:
            out.write(f"```\n{report}\n```\n")
    sys.exit(1 if any(line.startswith("FAIL") for line in lines) else 0)


if __name__ == "__main__":
    main()
