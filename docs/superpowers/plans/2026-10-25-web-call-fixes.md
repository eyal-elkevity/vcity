# Web Call Fixes: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser call at `/call` demo-worthy. Four problems Eyal hit on 2026-10-25 get fixed:

| # | Symptom | Cause (measured) |
|---|---|---|
| 1 | The page looks plain and isn't fun | It's a plain list under two buttons |
| 2 | Each agent sentence appears 10 to 14 times, but is spoken once | Pipecat 1.14 sends a protocol-2 client (the browser) one `bot-output` message **per spoken word**, each carrying the whole sentence with `spoken_status: "in-progress"`. The page appends every one. |
| 3 | The caller's question is split across lines, and background noise cuts the agent off | Deepgram sends one utterance as several finals, and the page gives each its own line. Any word (TV, "Hi.", "No.") counts as a barge-in, so the agent restarts its answer. |
| 4 | Numbers sound like gibberish ("section six point two t twenty") | ElevenLabs Flash v2.5 doesn't normalize numbers, and enabling that is Enterprise-only. |

**Architecture:**
- **Server, two changes in `vcity.voice.call`:**
  - A deterministic `speakable()` converter turns digits into words, used as a Pipecat TTS `text_transform`.
  - A `MinWordsUserTurnStartStrategy(min_words=2)` means a single word no longer interrupts the agent while it's talking.
- **Page:** `call.html` is rewritten as a "Circle City" page:
  - a lemon orb that shows the call state and reacts to audio levels
  - chat bubbles
  - one line per sentence, revealed as it's spoken and marked when cut off
  - one bubble per caller turn
- **Evals:** two new on-demand checks:
  - an `aside` case in the audio smoke test, a one-word "Okay." mid-answer
  - a speech round-trip eval: our converter, then ElevenLabs, then Deepgram

**Tech Stack:**
- Pipecat 1.14.0: `text_transforms` and `MinWordsUserTurnStartStrategy`
- `num2words` 0.5.14, already installed through Pipecat and now a direct dependency
- `@pipecat-ai/client-js@1.14.0` (RTVI protocol 2.2.0), loaded from esm.sh as before
- Google Fonts: Fraunces and Figtree

**Spec:** `docs/spec.md`:
- §9: latency and barge-in. "When the caller interrupts, playback stops" still holds, for two words or more.
- §10 layer 4: audio smoke

This plan is a detour Eyal asked for between M5 (merged) and the M6 deploy packaging. It's not a spec milestone.

## Decisions this plan makes (review these first)

All of these were prototyped on 2026-10-25 in a scratch clone, against the real Deepgram, ElevenLabs and Anthropic APIs. That includes driving the new page in Chrome with a recorded caller as the microphone.

| # | Question | Decision | Alternatives |
|---|---|---|---|
| D1 | What counts as an interruption? | While the agent is speaking, the caller must say **2 or more words** (`MinWordsUserTurnStartStrategy(min_words=2)`, Pipecat's own strategy). When the agent is silent, one word still starts the turn. "No, wait" interrupts; "Hi.", "Okay.", a cough or a TV line doesn't. The stop side is unchanged (Smart Turn v3). **Measured:** the new `aside` audio case ("Okay." 2 s into the answer) fails on `main`, where the agent is cut off once, and passes with D1. | 3 words, which misses "No, stop". Pipecat's VAD start, today's behaviour, where any sound interrupts. |
| D2 | How are numbers spoken? | `vcity.voice.speech.speakable(text)` rewrites digits as words before the text reaches ElevenLabs. It's a deterministic regex pipeline using `num2words`. The rules, in order: code sections (`6.20.020` → "six point twenty point oh twenty"), US phone numbers, including the E.164 `+1…` form the department directory stores (digit by digit, grouped), dollars, clock times, percentages, "Month day[, year]", years after "in/since/by/until/before/after", then any remaining number. The model's text, the history and the transcript keep the digits. | ElevenLabs `apply_text_normalization`, which is Enterprise-only on Flash. A prompt rule telling the model to write numbers as words, which hurts the scenario eval's exact-match checks and the readable transcript. Pipecat's `VoiceFormatter`, which has no code-section, year or phone-grouping rules we can trust. |
| D3 | How do we know the voice says them right? | A new on-demand eval, `python -m evals.speech`. Seven fixed phrases (from real answers) go through `speakable`, then ElevenLabs Flash, then Deepgram Nova-3, and must come back with the expected words. It runs in the manual `audio-smoke` workflow and costs about 1 cent. **Measured:** 7/7 with the converter. Raw text fails 3 of 5 (`6.20.040` → "point twenty d forty", the phone number, and `$1,250.50` → "two hundred and fifty dollar"). | Ears only. |
| D4 | One line per agent sentence | The page keys sentences by `segment_id`. A `"new"` message stores the written text. The first `"in-progress"` or `"completed"` message renders it, and the words are revealed in proportion to `spoken_progress` (accumulated against remaining words, which arrive in spoken form after D2). When the caller starts a turn, any sentence that isn't complete is truncated with a "— cut off" mark, and sentences never spoken are dropped. | Use `onBotTtsText` words, which would show the spoken form "six point twenty point oh twenty" instead of `6.20.020`. Downgrade the client to protocol 1, which just hides the bug. |
| D5 | One bubble per caller turn | A caller bubble opens on `onUserStartedSpeaking`. Finals join into it, and the live interim shows in italics. A transcript that arrives with no turn (an aside that D1 ignored) is held, then dropped as soon as the agent's speech continues. If a turn starts, it seeds the bubble. | One line per final, as today. |
| D6 | Look and feel | A "Circle City" theme (Corona's nickname; it was also a lemon town):<br>- a 196 px lemon orb as the call button, which shows `idle`, `connecting`, `listening` (a spinning dashed ring, sized by the mic level), `thinking` (a fast dotted ring) or `speaking` (pulses with the agent's audio level)<br>- chat bubbles<br>- a latency chip<br>- a transfer card<br>- four "things to try" chips while idle<br>Fraunces and Figtree from Google Fonts. Light and dark themes, a 390 px phone layout, and reduced motion are respected. No build step. | Keep the current layout with fixed bugs. A JS framework with a build. |
| D7 | Where does the server listen? | `python -m vcity` binds **127.0.0.1** by default. `HOST` and `PORT` come from the environment, so the Docker image (deploy plan) sets `HOST=0.0.0.0`. Today it binds every interface. That's needless exposure on a work laptop, after the 2026-10-25 security review. | Keep `0.0.0.0`. |
| D8 | Testing the page | There's no JS test harness (YAGNI). The page is verified by the controller in Chrome, through chrome-devtools, with `getUserMedia` replaced by a recorded caller. The recording is chickens, then the aside "Hi." mid-answer, then a weekend question that barges in. Expected:<br>- the aside leaves no bubble<br>- the first answer ends with "— cut off"<br>- every sentence appears once<br>- the last answer completes fully<br>Then Eyal tries it live. | Add a JS test runner, which is new tooling for one file. |
| D9 | What this plan doesn't do | Deploy packaging (Dockerfile, Render, memory growth) is the next plan. Phone telephony stays in the scratch prototype. The scenario eval and prompt are untouched. | — |

## Global Constraints

- Python 3.12, `uv run ruff check`, `uv run ruff format --check`, `uv run pyright` (strict) and `uv run pytest` all pass before **every** commit.
- No `# type: ignore` and no `Any` leaking out. An untyped library gets a stub in `typings/`.
- No comments that restate code, narrate a change, or mention tickets or TODOs. The allowed comments are external quirks, domain rules, spec magic numbers, and terse notes on genuinely non-obvious code.
- Conventional Commits with a body explaining why, ending with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3
  ```
- The repo is public: never commit secrets, `.env` or personal phone numbers. The phone numbers in the speech eval phrases are public City of Corona numbers from `data/departments.yaml`.
- Never log transcripts or exception messages, only type names. Pipecat's loguru stays at WARNING (`vcity.voice.logs.configure_logging`).
- Pipecat is pinned at `==1.14.0`. The page's JS client stays `@pipecat-ai/client-js@1.14.0` and `@pipecat-ai/websocket-transport@1.8.0`.
- Branch `web-call-fixes`, one PR. Eyal merges.

## File map

| File | Change |
|---|---|
| `pyproject.toml`, `uv.lock` | Add `num2words>=0.5.14` (Task 1) |
| `typings/num2words/__init__.pyi` | Create a typed stub (Task 1) |
| `src/vcity/voice/speech.py` | Create `speakable()` (Task 1) |
| `tests/voice/test_speech.py` | Create (Task 1) |
| `src/vcity/voice/call.py` | `SPEECH_MODEL`, the `text_transforms` hook (Task 1), and `INTERRUPT_MIN_WORDS` with the turn strategy (Task 2) |
| `evals/audio/{model,client,__main__}.py`, `evals/audio/cases.yaml`, `evals/audio/callers/aside.wav` | The aside case and a cut-off check (Task 2) |
| `tests/evals/test_audio_checks.py` | Create (Task 2) |
| `evals/speech/{__init__,__main__}.py`, `evals/speech/phrases.yaml` | Create the round-trip eval (Task 3) |
| `tests/evals/test_speech_checks.py` | Create (Task 3) |
| `.github/workflows/audio-smoke.yml` | Add an `evals.speech` step (Task 3) |
| `src/vcity/voice/static/call.html` | Rewrite (Task 4) |
| `src/vcity/__main__.py`, `tests/test_main.py` | Bind localhost by default (Task 5) |
| `README.md` | The voice-line and eval sections (Tasks 2–5) |

---

### Task 0 (controller, before dispatch): branch, plan, recording

- [ ] Create branch `web-call-fixes` from `main` in the real repo.
- [ ] Copy the prototype's recording `/private/tmp/claude-1820712540/-Users-eyalelkevity-Genoox-dev-vcity/706c8c0b-b371-4095-a155-02221e146ebb/scratchpad/aside.wav` to `evals/audio/callers/aside.wav`. It's 16 kHz mono, "Okay.", voice `nPczCjzI2devNBz1zQrb`, generated with ElevenLabs `eleven_flash_v2_5`, and the same caller voice as the other smoke clips.
- [ ] Commit the plan and the recording:

```bash
git add docs/superpowers/plans/2026-10-25-web-call-fixes.md evals/audio/callers/aside.wav
git commit -m "docs: add web call fixes plan and its aside recording" -m "The browser call repeated sentences, split questions, let any word cut the agent off and garbled numbers. The plan records the measured causes and the fixes; the one-word aside clip is what the audio smoke test plays mid-answer to prove a stray word no longer interrupts." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 1: Speak numbers as words

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Create: `typings/num2words/__init__.pyi`
- Create: `src/vcity/voice/speech.py`
- Create: `tests/voice/test_speech.py`
- Modify: `src/vcity/voice/call.py`

**Interfaces:**
- Produces:
  - `vcity.voice.speech.speakable(text: str) -> str`
  - `vcity.voice.call.SPEECH_MODEL = "eleven_flash_v2_5"`, which Task 3 imports

- [ ] **Step 1: Add the dependency and its stub**

Run: `uv add "num2words>=0.5.14"`

Create `typings/num2words/__init__.pyi`:

```python
def num2words(number: int, ordinal: bool = ..., lang: str = ..., to: str = ...) -> str: ...
```

- [ ] **Step 2: Write the failing tests** in `tests/voice/test_speech.py`

```python
import pytest

from vcity.voice.speech import speakable


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("section 6.20.020", "section six point twenty point oh twenty"),
        ("section 17.84.100.", "section seventeen point eighty four point one hundred."),
        ("(951) 736-2400", "nine five one, seven three six, two four zero zero"),
        ("951-736-2400", "nine five one, seven three six, two four zero zero"),
        ("call +19517362400.", "call nine five one, seven three six, two four zero zero."),
        ("+1 951-736-2400", "nine five one, seven three six, two four zero zero"),
        ("$25", "twenty five dollars"),
        ("$1", "one dollar"),
        ("$1,250.50", "one thousand two hundred and fifty dollars and fifty cents"),
        ("$40.00, due now", "forty dollars, due now"),
        ("at 5:30 PM.", "at five thirty P M."),
        ("8 a.m. to 5 p.m. daily", "eight A M to five P M daily"),
        ("at 9:05 am", "at nine oh five A M"),
        ("about 2.5% of", "about two point five percent of"),
        ("on October 27, 2026.", "on October twenty seventh, twenty twenty six."),
        ("on March 3rd", "on March third"),
        ("since 1998", "since nineteen ninety eight"),
        ("lots of 1,950 square feet", "lots of one thousand nine hundred and fifty square feet"),
        ("5,000 to 9,999 feet", "five thousand to nine thousand nine hundred and ninety nine feet"),
        ("at least 7,000, or", "at least seven thousand, or"),
        ("four hens and 10 feet", "four hens and ten feet"),
    ],
)
def test_numbers_are_written_out_as_spoken(written: str, spoken: str) -> None:
    assert speakable(written) == spoken


def test_text_without_numbers_is_unchanged() -> None:
    text = "Roosters aren't allowed, and eggs can't be sold."
    assert speakable(text) == text
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/voice/test_speech.py -q`
Expected: collection error, `No module named 'vcity.voice.speech'`.

- [ ] **Step 4: Implement `src/vcity/voice/speech.py`**

```python
import re
from collections.abc import Callable

from num2words import num2words

type Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]

_PHONE = r"(?:\+1[\s.-]?|(?<![\d+]))\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)"
_AMOUNT = r"\d{1,3}(?:,\d{3})+|\d+"
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def speakable(text: str) -> str:
    for pattern, say in _RULES:
        text = pattern.sub(say, text)
    return text


def _words(number: int) -> str:
    return num2words(number, lang="en").replace(",", "").replace("-", " ")


def _digits(digits: str) -> str:
    return " ".join(_words(int(digit)) for digit in digits)


def _padded(digits: str) -> str:
    if len(digits) > 1 and digits.startswith("0"):
        return f"oh {_words(int(digits))}"
    return _words(int(digits))


def _number(written: str) -> str:
    whole, _, fraction = written.replace(",", "").partition(".")
    spoken = _words(int(whole))
    return f"{spoken} point {_digits(fraction)}" if fraction else spoken


def _year(written: str) -> str:
    return num2words(int(written), lang="en", to="year").replace("-", " ")


def _section(match: re.Match[str]) -> str:
    return " point ".join(_padded(part) for part in match.groups())


def _phone(match: re.Match[str]) -> str:
    return ", ".join(_digits(group) for group in match.groups())


def _money(match: re.Match[str]) -> str:
    dollars = int(match[1].replace(",", ""))
    spoken = f"{_words(dollars)} dollar{'' if dollars == 1 else 's'}"
    if match[2] and int(match[2]):
        spoken += f" and {_words(int(match[2]))} cents"
    return spoken


def _time(match: re.Match[str]) -> str:
    hour, minutes, half = match[1], match[2], match[3].upper()
    spoken = _words(int(hour))
    if minutes and minutes != "00":
        spoken += f" {_padded(minutes)}"
    ends_sentence = match[0].endswith(".") and match.end() == len(match.string)
    return f"{spoken} {half} M{'.' if ends_sentence else ''}"


def _percent(match: re.Match[str]) -> str:
    return f"{_number(match[1])} percent"


def _date(match: re.Match[str]) -> str:
    day = num2words(int(match[2]), lang="en", to="ordinal").replace("-", " ")
    year = f", {_year(match[3])}" if match[3] else ""
    return f"{match[1]} {day}{year}"


def _year_after_preposition(match: re.Match[str]) -> str:
    return f"{match[1]} {_year(match[2])}"


_RULES: tuple[Rule, ...] = (
    (re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b"), _section),
    (re.compile(_PHONE), _phone),
    (re.compile(rf"\$({_AMOUNT})(?:\.(\d{{2}}))?"), _money),
    (re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.IGNORECASE), _time),
    (re.compile(r"(\d+(?:\.\d+)?)\s?%"), _percent),
    (re.compile(rf"\b({_MONTHS}) (\d{{1,2}})(?:st|nd|rd|th)?\b(?:, ((?:19|20)\d\d)\b)?"), _date),
    (re.compile(r"\b(in|since|by|until|before|after) ((?:19|20)\d\d)\b"), _year_after_preposition),
    (re.compile(rf"(?:{_AMOUNT})(?:\.\d+)?"), lambda match: _number(match[0])),
)
```

Notes for the implementer:
- **Rule order matters.** Sections, phones, money, times, percents and dates must run before the generic number rule, which would otherwise eat their digits.
- **`_AMOUNT`** matches either properly comma-grouped thousands or a plain digit run. A trailing comma ("7,000, or") stays punctuation.
- **A clock time** keeps its final "." only when it ends the text. Pipecat sends ElevenLabs one sentence at a time, so that "." is the sentence's full stop, and "a.m." mid-sentence loses its dots.
- **The phone rule** also takes `+19517362400`. That's how `data/departments.yaml` stores numbers, and without the rule it's read as "nineteen billion…".
- **Years** are only read as years after a month-day or a preposition, so "1,950 square feet" and "1950 square feet" stay quantities.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/voice/test_speech.py -q`
Expected: `22 passed`

- [ ] **Step 6: Wire it into the TTS** in `src/vcity/voice/call.py`

Add the import and the constant beside `SAMPLE_RATE`:

```python
from vcity.voice.speech import speakable
```

```python
SAMPLE_RATE = 24000
SPEECH_MODEL = "eleven_flash_v2_5"
```

Replace the `tts = ElevenLabsTTSService(...)` construction with:

```python
    tts = ElevenLabsTTSService(
        api_key=voice.elevenlabs_api_key.get_secret_value(),
        sample_rate=SAMPLE_RATE,
        settings=ElevenLabsTTSService.Settings(voice=voice.elevenlabs_voice_id, model=SPEECH_MODEL),
        text_transforms=[("*", _speakable)],
    )
```

Append at the end of the module:

```python
async def _speakable(text: str, _: object) -> str:
    return speakable(text)
```

`SPEECH_MODEL` was already Pipecat's default for this service. Pinning it makes the speech eval (Task 3) test the model the call uses.

- [ ] **Step 7: All checks**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock typings/num2words src/vcity/voice/speech.py src/vcity/voice/call.py tests/voice/test_speech.py
git commit -m "fix: say numbers as words on the voice line" -m "ElevenLabs Flash reads digits literally and its normalization is Enterprise-only, so code sections came out as 'six point two t twenty' and amounts lost their thousands. A deterministic converter now writes sections, phone numbers, money, times, percentages, dates and plain numbers as words before synthesis; the transcript and history keep the digits." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 2: Only two or more words interrupt the agent

**Files:**
- Modify: `src/vcity/voice/call.py`
- Modify: `evals/audio/model.py`, `evals/audio/client.py`, `evals/audio/__main__.py`, `evals/audio/cases.yaml`
- Create: `tests/evals/test_audio_checks.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `evals/audio/callers/aside.wav`, from Task 0
- Produces:
  - `evals.audio.model.Aside(wav: Path, after: float)` and `AudioCase.aside: Aside | None`
  - `evals.audio.client.call(url, wav, aside: tuple[Path, float] | None = None) -> Heard`
  - `Heard.interruptions: int`, `Heard.answered_at: float | None`, `Heard.answered_for(seconds) -> bool`

- [ ] **Step 1: Write the failing tests** in `tests/evals/test_audio_checks.py`

```python
from pathlib import Path

from evals.audio.__main__ import problems
from evals.audio.client import Heard
from evals.audio.model import AudioCase, Expect, load_cases

CASE = AudioCase(id="chickens", wav=Path("c.wav"), says="", expect=Expect(mentions=("hen",)))


def test_an_answer_that_was_never_cut_off_passes() -> None:
    heard = Heard(first_audio_seconds=1.1, agent_said=["Hens are allowed."])
    assert problems(CASE, heard) == []


def test_an_answer_cut_off_by_an_aside_fails() -> None:
    heard = Heard(first_audio_seconds=1.1, agent_said=["Hens are"], interruptions=1)
    assert problems(CASE, heard) == ["the agent was cut off 1 time(s)"]


def test_the_aside_case_plays_a_short_word_after_the_answer_starts() -> None:
    aside = next(case for case in load_cases() if case.id == "aside").aside
    assert aside is not None
    assert aside.wav == Path("callers/aside.wav")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/evals/test_audio_checks.py -q`
Expected: FAIL, because `Heard` has no `interruptions` field and there's no `aside` case.

- [ ] **Step 3: The aside case and the cut-off check**

In `evals/audio/model.py`, add above `AudioCase`:

```python
class Aside(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wav: Path
    after: float
```

and add the field to `AudioCase`, between `says` and `expect`:

```python
    aside: Aside | None = None
```

Append to `evals/audio/cases.yaml`:

```yaml
- id: aside
  wav: callers/chickens.wav
  says: "Hi. Can I keep chickens in my backyard?"
  aside: {wav: callers/aside.wav, after: 2.0}
  expect: {mentions: ["6.20|chicken|hen"]}
```

Replace `evals/audio/client.py` with:

```python
import asyncio
import contextlib
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import websockets
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputTransportMessageFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pydantic import BaseModel, ValidationError

CHUNK_SECONDS = 0.02
LEAD_SILENCE_SECONDS = 0.5
LISTEN_SECONDS = 12.0
CLIENT_READY = {
    "label": "rtvi-ai",
    "type": "client-ready",
    "data": {"version": "1.0.0", "about": {"library": "vcity-audio-smoke"}},
}


class _Message(BaseModel):
    type: str
    data: dict[str, object] = {}


@dataclass
class Heard:
    first_audio_seconds: float | None = None
    caller_transcript: list[str] = field(default_factory=list[str])
    agent_said: list[str] = field(default_factory=list[str])
    transfers: list[str] = field(default_factory=list[str])
    interruptions: int = 0
    answered_at: float | None = None

    @property
    def spoken(self) -> str:
        return " ".join(self.agent_said)

    def answered_for(self, seconds: float) -> bool:
        return self.answered_at is not None and time.perf_counter() - self.answered_at >= seconds


async def call(url: str, wav: Path, aside: tuple[Path, float] | None = None) -> Heard:
    rate, pcm = _read(wav)
    serializer = ProtobufFrameSerializer()
    heard = Heard()
    step = int(rate * CHUNK_SECONDS) * 2
    async with websockets.connect(url, max_size=None) as socket:

        async def send(frame: OutputAudioRawFrame | OutputTransportMessageUrgentFrame) -> None:
            if data := await serializer.serialize(frame):
                await socket.send(data)

        async def stream(audio: bytes) -> None:
            for start in range(0, len(audio), step):
                await send(OutputAudioRawFrame(audio[start : start + step], rate, 1))
                await asyncio.sleep(CHUNK_SECONDS)

        async def listen(spoke_at: list[float]) -> None:
            async for raw in socket:
                frame = await serializer.deserialize(raw)
                if (
                    isinstance(frame, InputAudioRawFrame)
                    and spoke_at
                    and heard.first_audio_seconds is None
                ):
                    heard.answered_at = time.perf_counter()
                    heard.first_audio_seconds = heard.answered_at - spoke_at[0]
                elif isinstance(frame, InputTransportMessageFrame):
                    _record(heard, frame.message)

        await send(
            OutputTransportMessageUrgentFrame(message={**CLIENT_READY, "id": uuid.uuid4().hex})
        )
        spoke_at: list[float] = []
        listener = asyncio.create_task(listen(spoke_at))
        silence = bytes(step)
        await stream(silence * int(LEAD_SILENCE_SECONDS / CHUNK_SECONDS))
        await stream(pcm)
        spoke_at.append(time.perf_counter())
        with contextlib.suppress(websockets.ConnectionClosed):
            if aside is not None:
                aside_wav, after = aside
                await _idle_until_answered_for(heard, after, lambda: stream(silence))
                await stream(_read(aside_wav)[1])
            await stream(silence * int(LISTEN_SECONDS / CHUNK_SECONDS))
        listener.cancel()
    return heard


def _read(wav: Path) -> tuple[int, bytes]:
    with wave.open(str(wav), "rb") as clip:
        return clip.getframerate(), clip.readframes(clip.getnframes())


async def _idle_until_answered_for(
    heard: Heard, seconds: float, idle: Callable[[], Awaitable[None]]
) -> None:
    deadline = time.perf_counter() + LISTEN_SECONDS
    while not heard.answered_for(seconds) and time.perf_counter() < deadline:
        await idle()


def _record(heard: Heard, raw: object) -> None:
    try:
        message = _Message.model_validate(raw)
    except ValidationError:
        return
    match message.type, message.data:
        case "user-transcription", {"final": True, "text": str(text)}:
            heard.caller_transcript.append(text)
        case "bot-output", {"aggregated_by": "sentence", "text": str(text)}:
            heard.agent_said.append(text)
        case "server-message", {"type": "transfer", "department": str(department)}:
            heard.transfers.append(department)
        case "bot-interrupted", _ if heard.answered_at is not None:
            heard.interruptions += 1
        case _:
            pass
```

In `evals/audio/__main__.py`, append to `problems()` just before `return found`:

```python
    if heard.interruptions:
        found.append(f"the agent was cut off {heard.interruptions} time(s)")
```

and in `run()` replace `heard = await call(url, HERE / case.wav)` with:

```python
        aside = (HERE / case.aside.wav, case.aside.after) if case.aside else None
        heard = await call(url, HERE / case.wav, aside)
```

`bot-interrupted` is counted only after the agent's first audio. Pipecat also sends one when the caller's first turn starts, before the agent has said anything.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/evals/test_audio_checks.py -q`
Expected: `3 passed`

- [ ] **Step 5: The interruption threshold** in `src/vcity/voice/call.py`

Add the imports, keeping ruff's isort order: `transports` sorts before `turns`.

```python
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
```

Add the constant beside `SAMPLE_RATE`:

```python
INTERRUPT_MIN_WORDS = 2
```

Replace the aggregator pair construction with:

```python
    user, assistant = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=INTERRUPT_MIN_WORDS)]
            ),
        ),
    )
```

The stop strategies stay Pipecat's default (Smart Turn v3 with the Silero VAD).

- [ ] **Step 6: README**

In `README.md` under "The voice line", replace the **Interruptions** bullet with:

```markdown
- **Interruptions:** two or more words over the agent stop playback, cancel the reply, and keep only what you actually heard in the conversation. A single word ("okay", a cough, a TV in the room) doesn't interrupt.
```

and under "Audio smoke test" replace the paragraph with:

```markdown
Plays the recorded callers in `evals/audio/callers/` into the voice line in real time. Each passes if the agent says what's expected (or transfers), its first audio arrives within 2 s of the caller going quiet, and nothing cuts it off. The `aside` case says "Okay." two seconds into the answer to prove one word doesn't interrupt. The **audio-smoke** workflow runs it on demand. It needs the repo secrets `DEEPGRAM_API_KEY` and `ELEVENLABS_API_KEY`.
```

- [ ] **Step 7: All checks**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/vcity/voice/call.py evals/audio tests/evals/test_audio_checks.py README.md
git commit -m "fix: let only two or more words interrupt the agent" -m "Any word Deepgram heard counted as a barge-in, so a TV, a cough or an 'okay' made the agent drop its answer and start again. While the agent speaks the caller now needs two words; when it is silent one word still starts the turn. The audio smoke test gains an aside case that says 'Okay.' mid-answer and fails if the agent is cut off, which it was on main." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 3: Speech round-trip eval

**Files:**
- Create: `evals/speech/__init__.py` (empty), `evals/speech/__main__.py`, `evals/speech/phrases.yaml`
- Create: `tests/evals/test_speech_checks.py`
- Modify: `.github/workflows/audio-smoke.yml`, `README.md`

**Interfaces:**
- Consumes: `vcity.voice.speech.speakable` and `vcity.voice.call.SPEECH_MODEL` (Task 1), and `vcity.settings.VoiceSettings`
- Produces: `evals.speech.__main__.Phrase(text: str, heard: tuple[str, ...])` and `missing(phrase, transcript) -> list[str]`

- [ ] **Step 1: Write the failing tests** in `tests/evals/test_speech_checks.py`

```python
from evals.speech.__main__ import Phrase, missing


def test_every_expected_reading_must_be_heard() -> None:
    phrase = Phrase(text="", heard=("five thousand", "nine nine"))
    assert missing(phrase, "Lots of five-thousand square feet.") == ["nine nine"]


def test_any_alternative_reading_counts() -> None:
    phrase = Phrase(text="", heard=("point o twenty|point oh twenty",))
    assert missing(phrase, "six point oh twenty") == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/evals/test_speech_checks.py -q`
Expected: collection error, `No module named 'evals.speech'`.

- [ ] **Step 3: Implement**

Create an empty `evals/speech/__init__.py`.

`evals/speech/phrases.yaml`:

```yaml
- text: "Under Corona Municipal Code section 6.20.020, you can keep hens."
  heard: ["six point twenty point o twenty|six point twenty point oh twenty"]
- text: "Lots of 5,000 to 9,999 square feet can have four hens."
  heard: ["five thousand", "nine thousand nine hundred and ninety nine|nine thousand nine hundred ninety nine"]
- text: "The next meeting is on Tuesday, October 27, at 5:30 PM."
  heard: ["october twenty seventh", "five thirty"]
- text: "You can reach Public Works at (951) 736-2400."
  heard: ["nine five one seven three six two four zero zero"]
- text: "The Building Division's number is +19517362250."
  heard: ["nine five one seven three six two two five zero"]
- text: "The permit fee is $1,250.50, about 2.5% of the project."
  heard: ["one thousand two hundred and fifty dollars|one thousand two hundred fifty dollars", "fifty cents", "two point five percent"]
- text: "City Hall is open 8 a.m. to 5 p.m."
  heard: ["eight am", "five pm"]
```

`evals/speech/__main__.py`:

```python
import asyncio
import re
import sys
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict

from vcity.settings import VoiceSettings
from vcity.voice.call import SPEECH_MODEL
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
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice.elevenlabs_voice_id}",
        params={"output_format": f"pcm_{SAMPLE_RATE}"},
        headers={"xi-api-key": voice.elevenlabs_api_key.get_secret_value()},
        json={"text": speakable(text), "model_id": SPEECH_MODEL},
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
```

Deepgram writes the spoken "oh" as "o", so the expected readings list both.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/evals/test_speech_checks.py -q`
Expected: `2 passed`

- [ ] **Step 5: Workflow and README**

In `.github/workflows/audio-smoke.yml`, add after `- run: uv run python -m evals.audio`:

```yaml
      - run: uv run python -m evals.speech
```

In `README.md`, add after the "Audio smoke test" section:

````markdown
## Speech check

```bash
uv run python -m evals.speech
```

Sends the phrases in `evals/speech/phrases.yaml` through the same number-to-words step and ElevenLabs voice the call uses, transcribes the audio with Deepgram, and checks that section numbers, amounts, times and phone numbers come back as the expected words. The **audio-smoke** workflow runs it too.
````

- [ ] **Step 6: All checks**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add evals/speech tests/evals/test_speech_checks.py .github/workflows/audio-smoke.yml README.md
git commit -m "test: check that numbers survive the trip through the voice" -m "Unit tests pin what the converter writes, but only the voice and a transcriber can show that 'six point twenty point oh twenty' is what a caller hears. The speech check synthesizes real answer phrases with the call's ElevenLabs model, transcribes them with Deepgram and expects the spoken words back; it runs on demand with the audio smoke test." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 4: The Circle City call page

**Files:**
- Modify: `src/vcity/voice/static/call.html` (full rewrite)
- Modify: `README.md`

**Interfaces:**
- Consumes the server's existing RTVI traffic:
  - `bot-output`, with `segment_id`, `spoken_status`, `spoken_progress` and `will_be_spoken`
  - `user-transcription`
  - `user-` and `bot-started-speaking` / `stopped-speaking`
  - audio levels
  - the server messages `{"type": "latency", "total"}` and `{"type": "transfer", "department"}`
- The page still connects to `/ws/browser`, which `tests/test_app.py::test_call_page_connects_to_the_browser_socket` asserts.

- [ ] **Step 1: Replace `src/vcity/voice/static/call.html`** with:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Corona City Line</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Figtree:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #fbf7ec; --bg-glow: #fff1b8; --ink: #1f2a1f; --muted: #66705f;
    --leaf: #2f6b3a; --leaf-soft: #e3eedd; --lemon: #f4c430; --lemon-deep: #e0a800;
    --card: #ffffff; --line: #e8e1cc; --you: #2f6b3a; --you-ink: #ffffff; --danger: #c2412d;
    --shadow: 0 10px 30px rgba(60, 50, 10, .10);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #141a14; --bg-glow: #2a2a12; --ink: #eef2e8; --muted: #a3ad9b;
      --leaf: #7cc48a; --leaf-soft: #1f2d21; --lemon: #f4c430; --lemon-deep: #ffd75a;
      --card: #1b231c; --line: #2b362c; --you: #3d7d49; --you-ink: #ffffff; --danger: #ef6b55;
      --shadow: 0 10px 30px rgba(0, 0, 0, .35);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; color: var(--ink);
    background: radial-gradient(1200px 600px at 50% -10%, var(--bg-glow), transparent 60%), var(--bg);
    font: 16px/1.5 Figtree, system-ui, sans-serif;
  }
  main { max-width: 680px; margin: 0 auto; padding: 28px 16px 48px; }
  header { display: flex; align-items: center; gap: 12px; }
  header svg { flex: none; }
  h1 { font: 700 1.6rem/1.1 Fraunces, Georgia, serif; margin: 0; letter-spacing: -.01em; }
  .tagline { color: var(--muted); margin: 2px 0 0; font-size: .95rem; }

  .stage { display: grid; justify-items: center; gap: 14px; margin: 28px 0 8px; }
  .orb {
    --level: 0; position: relative; width: 196px; height: 196px; border-radius: 50%; border: 0; padding: 0;
    background: radial-gradient(circle at 35% 30%, #fff6c9, var(--lemon) 55%, var(--lemon-deep));
    box-shadow: var(--shadow), 0 0 0 calc(6px + var(--level) * 26px) color-mix(in srgb, var(--lemon) 28%, transparent);
    transition: box-shadow 90ms linear, transform 200ms ease; cursor: pointer; color: #3b2f00;
  }
  .orb:focus-visible { outline: 3px solid var(--leaf); outline-offset: 6px; }
  .orb:hover:not([disabled]) { transform: scale(1.03); }
  .orb[disabled] { cursor: default; }
  .orb .ring { position: absolute; inset: -14px; border-radius: 50%; border: 2px dashed color-mix(in srgb, var(--leaf) 55%, transparent); opacity: 0; }
  .orb .label { font: 600 1.05rem Figtree, sans-serif; }
  .orb .icon { display: block; margin: 0 auto 6px; }
  [data-state="listening"] .orb { box-shadow: var(--shadow), 0 0 0 calc(6px + var(--level) * 26px) color-mix(in srgb, var(--leaf) 30%, transparent); }
  [data-state="listening"] .orb .ring { opacity: 1; animation: spin 12s linear infinite; }
  [data-state="thinking"] .orb .ring { opacity: 1; border-style: dotted; border-width: 4px; animation: spin 1.6s linear infinite; }
  [data-state="speaking"] .orb { transform: scale(calc(1 + var(--level) * .06)); }
  [data-state="connecting"] .orb { animation: breathe 1.4s ease-in-out infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes breathe { 50% { transform: scale(.96); } }

  .status { font-weight: 600; color: var(--leaf); min-height: 1.5em; text-align: center; }
  .controls { display: flex; gap: 10px; align-items: center; min-height: 44px; }
  button.hangup { font: 600 1rem Figtree, sans-serif; border: 0; border-radius: 999px; padding: 10px 22px; background: var(--danger); color: #fff; cursor: pointer; }
  button.hangup:focus-visible { outline: 3px solid var(--ink); outline-offset: 3px; }
  .chip { font-size: .85rem; color: var(--muted); background: var(--card); border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px; }
  [hidden] { display: none !important; }

  .suggestions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin: 4px 0 0; padding: 0; list-style: none; }
  .suggestions li { font-size: .9rem; background: var(--leaf-soft); color: var(--ink); border-radius: 999px; padding: 6px 14px; }

  .transfer { display: flex; gap: 14px; align-items: center; background: var(--card); border: 2px solid var(--leaf); border-radius: 16px; padding: 14px 16px; margin: 20px 0 0; box-shadow: var(--shadow); }
  .transfer strong { display: block; font: 600 1.05rem Fraunces, Georgia, serif; }
  .transfer span { color: var(--muted); font-size: .92rem; }

  .chat { display: flex; flex-direction: column; gap: 10px; margin-top: 24px; }
  .bubble { max-width: 85%; padding: 10px 14px; border-radius: 18px; box-shadow: 0 1px 2px rgba(0,0,0,.06); overflow-wrap: anywhere; }
  .bubble.city { align-self: flex-start; background: var(--card); border: 1px solid var(--line); border-bottom-left-radius: 6px; }
  .bubble.you { align-self: flex-end; background: var(--you); color: var(--you-ink); border-bottom-right-radius: 6px; }
  .who { display: block; font-size: .75rem; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; opacity: .7; margin-bottom: 2px; }
  .unspoken { opacity: .35; }
  .interim { opacity: .65; font-style: italic; }
  .cut::after { content: " — cut off"; font-size: .8rem; font-style: italic; opacity: .6; }
  footer { margin-top: 32px; color: var(--muted); font-size: .85rem; text-align: center; }
  @media (max-width: 480px) {
    .orb { width: 164px; height: 164px; }
    .bubble { max-width: 92%; }
  }
  @media (prefers-reduced-motion: reduce) {
    .orb, .orb .ring { animation: none !important; transition: none; }
  }
</style>
</head>
<body>
<main data-state="idle">
  <header>
    <svg width="44" height="44" viewBox="0 0 44 44" aria-hidden="true">
      <circle cx="22" cy="22" r="19" fill="none" stroke="var(--leaf)" stroke-width="4"/>
      <circle cx="22" cy="22" r="9" fill="var(--lemon)"/>
      <path d="M22 13c4-5 9-5 11-3-3 1-6 3-11 3z" fill="var(--leaf)"/>
    </svg>
    <div>
      <h1>Corona City Line</h1>
      <p class="tagline">Ask the Circle City anything — by voice, right from your browser.</p>
    </div>
  </header>

  <section class="stage" aria-live="polite">
    <button class="orb" id="orb" type="button" aria-describedby="status">
      <span class="ring" aria-hidden="true"></span>
      <svg class="icon" width="34" height="34" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.6a1 1 0 0 1-.25 1z"/></svg>
      <span class="label" id="orb-label">Call the city</span>
    </button>
    <div class="status" id="status">Tap the lemon to start a call.</div>
    <div class="controls">
      <button class="hangup" id="hangup" type="button" hidden>Hang up</button>
      <span class="chip" id="latency" hidden></span>
    </div>
    <ul class="suggestions" id="suggestions" aria-label="Things to try">
      <li>“Can I keep chickens in my backyard?”</li>
      <li>“What's happening this weekend?”</li>
      <li>“There's a pothole on my street.”</li>
      <li>“Connect me to the police non-emergency line.”</li>
    </ul>
  </section>

  <div class="transfer" id="transfer" hidden>
    <svg width="36" height="36" viewBox="0 0 24 24" aria-hidden="true"><path fill="var(--leaf)" d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.6a1 1 0 0 1-.25 1z"/></svg>
    <div>
      <strong id="transfer-title"></strong>
      <span>On a phone call, this is where the city line dials the department.</span>
    </div>
  </div>

  <section class="chat" id="chat" aria-label="Call transcript"></section>
  <footer>Answers come from public City of Corona pages and the municipal code.</footer>
  <audio id="bot" autoplay></audio>
</main>
<script type="module">
import { PipecatClient } from "https://esm.sh/@pipecat-ai/client-js@1.14.0";
import { WebSocketTransport } from "https://esm.sh/@pipecat-ai/websocket-transport@1.8.0?deps=@pipecat-ai/client-js@1.14.0";

const main = document.querySelector("main");
const orb = document.getElementById("orb");
const orbLabel = document.getElementById("orb-label");
const status = document.getElementById("status");
const hangup = document.getElementById("hangup");
const latency = document.getElementById("latency");
const suggestions = document.getElementById("suggestions");
const transfer = document.getElementById("transfer");
const transferTitle = document.getElementById("transfer-title");
const chat = document.getElementById("chat");
const bot = document.getElementById("bot");

const STATES = {
  idle: ["Call the city", "Tap the lemon to start a call."],
  connecting: ["Connecting…", "Allow the microphone if your browser asks."],
  listening: ["Listening", "Go ahead, I'm listening."],
  thinking: ["Checking…", "Looking that up."],
  speaking: ["Speaking", "Interrupt any time."],
  ended: ["Call again", "Call ended."],
};

function setState(state) {
  main.dataset.state = state;
  const [label, text] = STATES[state];
  orbLabel.textContent = label;
  status.textContent = text;
  const live = state !== "idle" && state !== "ended";
  orb.disabled = live;
  hangup.hidden = !live || state === "connecting";
  suggestions.hidden = live;
}

function bubble(kind) {
  const el = document.createElement("div");
  el.className = `bubble ${kind}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = kind === "you" ? "You" : "City";
  el.append(who);
  chat.append(el);
  el.scrollIntoView({ block: "end", behavior: "smooth" });
  return el;
}

const words = (text) => text.split(/\s+/).filter(Boolean);

class Sentence {
  constructor(text, into) {
    this.words = words(text);
    this.spoken = document.createElement("span");
    this.rest = document.createElement("span");
    this.rest.className = "unspoken";
    const el = document.createElement("span");
    el.append(this.spoken, this.rest, document.createTextNode(" "));
    into.append(el);
    this.el = el;
    this.done = false;
    this.show(0);
  }
  show(count) {
    this.count = count;
    this.spoken.textContent = this.words.slice(0, count).join(" ");
    const rest = this.words.slice(count).join(" ");
    this.rest.textContent = count && rest ? ` ${rest}` : rest;
  }
  progress({ accumulated_text, remaining_text }) {
    const said = words(accumulated_text).length;
    const total = said + words(remaining_text).length;
    this.show(Math.round((said / Math.max(total, 1)) * this.words.length));
  }
  complete() { this.done = true; this.show(this.words.length); }
  cut() {
    if (this.done) return;
    this.rest.remove();
    if (this.count === 0) this.el.remove();
    else this.el.classList.add("cut");
  }
}

const transcript = {
  city: null,
  you: null,
  finals: [],
  heard: "",
  pending: new Map(),
  sentences: new Map(),

  botOutput(data) {
    if ((data.text_type ?? data.aggregated_by) !== "sentence" || data.segment_id == null) return;
    if (data.spoken_status === "new") {
      if (data.will_be_spoken) this.pending.set(data.segment_id, data.text);
      return;
    }
    this.heard = "";
    let sentence = this.sentences.get(data.segment_id);
    if (!sentence) {
      this.you = null;
      this.city ??= bubble("city");
      sentence = new Sentence(this.pending.get(data.segment_id) ?? data.text, this.city);
      this.pending.delete(data.segment_id);
      this.sentences.set(data.segment_id, sentence);
    }
    if (data.spoken_status === "completed") sentence.complete();
    else if (data.spoken_progress) sentence.progress(data.spoken_progress);
  },

  userStarted() {
    for (const sentence of this.sentences.values()) sentence.cut();
    this.sentences.clear();
    this.pending.clear();
    this.city = null;
    if (!this.you) {
      this.you = bubble("you");
      this.finals = [];
      this.youText = document.createElement("span");
      this.youInterim = document.createElement("span");
      this.youInterim.className = "interim";
      this.you.append(this.youText, this.youInterim);
    }
    this.renderYou(this.heard);
    this.heard = "";
  },

  userTranscript({ text, final }) {
    if (!this.you) {
      this.heard = text;
      return;
    }
    if (final) this.finals.push(text);
    this.renderYou(final ? "" : text);
  },

  renderYou(interim) {
    this.youText.textContent = this.finals.join(" ");
    this.youInterim.textContent = (this.finals.length && interim ? " " : "") + interim;
  },

  reset() {
    chat.replaceChildren();
    Object.assign(this, { city: null, you: null, finals: [], heard: "" });
    this.pending.clear();
    this.sentences.clear();
  },
};

const client = new PipecatClient({
  transport: new WebSocketTransport(),
  enableMic: true,
  enableCam: false,
  callbacks: {
    onConnected: () => setState("listening"),
    onDisconnected: () => { setState("ended"); orb.style.setProperty("--level", 0); },
    onUserStartedSpeaking: () => { transcript.userStarted(); setState("listening"); },
    onUserStoppedSpeaking: () => setState("thinking"),
    onBotStartedSpeaking: () => setState("speaking"),
    onBotStoppedSpeaking: () => setState("listening"),
    onUserTranscript: (data) => transcript.userTranscript(data),
    onBotOutput: (data) => transcript.botOutput(data),
    onLocalAudioLevel: (level) => { if (main.dataset.state === "listening") orb.style.setProperty("--level", Math.min(level * 4, 1)); },
    onRemoteAudioLevel: (level) => { if (main.dataset.state === "speaking") orb.style.setProperty("--level", Math.min(level * 4, 1)); },
    onTrackStarted: (track, participant) => {
      if (!participant?.local && track.kind === "audio") bot.srcObject = new MediaStream([track]);
    },
    onServerMessage: (message) => {
      if (message.type === "latency") {
        latency.textContent = `Replied in ${message.total.toFixed(2)} s`;
        latency.hidden = false;
      }
      if (message.type === "transfer") {
        transferTitle.textContent = `Transferring you to ${message.department.replaceAll("_", " ")}`;
        transfer.hidden = false;
      }
    },
  },
});

orb.addEventListener("click", async () => {
  transcript.reset();
  transfer.hidden = true;
  latency.hidden = true;
  setState("connecting");
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  try {
    await client.connect({ wsUrl: `${scheme}://${location.host}/ws/browser` });
  } catch (error) {
    setState("idle");
    status.textContent = error?.name === "NotAllowedError"
      ? "Microphone access was blocked. Allow it and try again."
      : "Couldn't connect. Try again.";
  }
});
hangup.addEventListener("click", () => client.disconnect());
setState("idle");
</script>
</body>
</html>
```

How the transcript logic maps to decisions D4 and D5:
- `Sentence.progress()` reveals the written words in proportion to the spoken-form progress. After Task 1 the spoken form has more words ("6.20.020" is 6 words), so a count can't be used.
- `userStarted()` cuts or drops unfinished sentences. That's the same moment the server cancels the reply, because the turn-start strategy fires the interruption.
- `heard` holds a transcript that arrived without a turn. Any further agent speech clears it, because D1 ignored that word.

- [ ] **Step 2: README**

Replace the **Page** bullet under "The voice line" with:

```markdown
- **Page:** `/call` streams your microphone to the server over a WebSocket, with Pipecat's browser client. The lemon shows whether the line is listening, thinking or speaking. The transcript shows each sentence once, as it's spoken, and marks the ones you cut off.
```

- [ ] **Step 3: All checks**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`
Expected: all pass. `test_call_page_connects_to_the_browser_socket` still finds `/ws/browser`.

- [ ] **Step 4: Commit**

```bash
git add src/vcity/voice/static/call.html README.md
git commit -m "feat: redesign the call page and show each sentence once" -m "Pipecat's protocol-2 client gets a bot-output message per spoken word, each repeating its sentence, and the page appended every one, so answers appeared a dozen times. Sentences are now keyed by segment and revealed as they are spoken, cut-off sentences are marked, and the caller gets one bubble per turn. The page also becomes a Circle City design: a lemon orb that shows listening, thinking and speaking, chat bubbles, a latency chip and a transfer card." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 5: Listen only on this machine by default

**Files:**
- Modify: `src/vcity/__main__.py`
- Create: `tests/test_main.py`
- Modify: `README.md`

**Interfaces:**
- Produces: the env vars `HOST` (default `127.0.0.1`) and `PORT` (default `8080`), which the deploy plan's Docker image sets

- [ ] **Step 1: Write the failing tests** in `tests/test_main.py`

```python
import pytest
from fastapi import FastAPI

from vcity import __main__ as entry


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def run(_: FastAPI, *, host: str, port: int) -> None:
        seen.update(host=host, port=port)

    def create_app(*_: object) -> FastAPI:
        return FastAPI()

    monkeypatch.setattr(entry, "create_app", create_app)
    monkeypatch.setattr(entry.AppSettings, "from_env", lambda: None)
    monkeypatch.setattr(entry.VoiceSettings, "from_env", lambda: None)
    monkeypatch.setattr(entry.uvicorn, "run", run)
    return seen


def test_the_server_listens_only_on_this_machine_by_default(
    served: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    entry.main()
    assert served == {"host": "127.0.0.1", "port": 8080}


def test_host_and_port_come_from_the_environment(
    served: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("PORT", "9000")
    entry.main()
    assert served == {"host": "0.0.0.0", "port": 9000}  # noqa: S104
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_main.py -q`
Expected: the first test FAILS with `host` `'0.0.0.0'` against `'127.0.0.1'`.

- [ ] **Step 3: Implement.** Replace `src/vcity/__main__.py` with:

```python
import os

import uvicorn

from vcity.app import create_app
from vcity.settings import AppSettings, VoiceSettings
from vcity.voice.logs import configure_logging

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def main() -> None:
    configure_logging()
    app = create_app(AppSettings.from_env(), VoiceSettings.from_env())
    uvicorn.run(
        app,
        host=os.environ.get("HOST", DEFAULT_HOST),
        port=int(os.environ.get("PORT", DEFAULT_PORT)),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_main.py -q`
Expected: `2 passed`

- [ ] **Step 5: README.** Under "The voice line", after the code block, add:

```markdown
The server listens on `127.0.0.1:8080`. Set `HOST` and `PORT` to change that (a container sets `HOST=0.0.0.0`).
```

- [ ] **Step 6: All checks, then commit**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`

```bash
git add src/vcity/__main__.py tests/test_main.py README.md
git commit -m "fix: listen only on this machine unless HOST says otherwise" -m "The server bound every network interface, so anyone on the same network could reach a dev machine's voice line and spend its API keys. It now defaults to 127.0.0.1; HOST and PORT come from the environment so a container can still bind 0.0.0.0." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 6 (controller): live verification, then the PR

- [ ] Start the branch server on localhost: `uv run python -m vcity`. It now binds 127.0.0.1 by default.
- [ ] `uv run python -m evals.audio`. Expected: 4/4 PASS (chickens, weekend, connect, aside), with first audio ≤ 2 s.
- [ ] `uv run python -m evals.speech`. Expected: 7/7 PASS.
- [ ] Browser check through chrome-devtools (D8):
  - Install the fake microphone from `.superpowers/uiprobe/fake_mic.js`. It's git-ignored and already built from `chickens.wav`, the "Hi." aside and `weekend.wav`.
  - Click the orb and wait 30 s.
  - Expected:
    - two "You" bubbles and two "City" bubbles
    - the first City bubble ends with the cut-off mark
    - no bubble for "Hi."
    - no duplicate sentences
    - the last answer is fully spoken, with no `.unspoken` text left
  - Take screenshots: light at desktop width, and dark at 390 px.
- [ ] Ask Eyal to try `/call` live: speakers on, a TV nearby is fine.
- [ ] Final whole-branch review (most capable model), one fix wave, then open the PR. The body is this plan's TL;DR: the symptom/cause table, the decisions, and the measured results. It ends with the 🤖 Claude Code footer and the session link.
