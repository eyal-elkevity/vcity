# Aura-2 Voice with Word Timing: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ElevenLabs Flash with Deepgram Aura-2 ("Thalia") as the call's voice without losing the three things ElevenLabs' word timestamps gave us:
- the word-by-word reveal on the page
- an exact "cut off" point when the caller barges in
- a conversation history that keeps only what the caller actually heard

**Why now:** the ElevenLabs free plan (10,000 characters a month) ran out on 2026-10-25. Aura-2 is billed against the existing Deepgram credit, which stood at $199.86 that day. It costs $0.030 per 1,000 characters, so all of 2026-10-25's usage would have cost about $0.30.

**Architecture:**
- **Problem:** Deepgram's speak WebSocket returns audio, but no word timings.
- **The new service:** `TimedDeepgramTTSService` subclasses Pipecat's `DeepgramTTSService`.
  - It sends `Speak` and then `Flush` for **each sentence**, so Deepgram's `Flushed` message marks where that sentence's audio ends.
  - As each sentence's audio arrives, a `SentencePacer` releases its words at estimated start times. The estimate uses a seconds-per-letter rate learned from earlier sentences.
  - On `Flushed`, any words not yet released are fitted into the sentence's real audio length.
  - Offsets accumulate across the turn.
- **What Pipecat does with that:** it treats these as ordinary word timestamps (`add_word_timestamps`), so the existing playback-synced machinery drives three things unchanged:
  - RTVI `spoken_progress` for the page
  - interruption truncation
  - `Conversation.heard()`

**Tech Stack:**
- Pipecat 1.14.0 `DeepgramTTSService`, used through its `run_tts`, `flush_audio`, `on_audio_context_interrupted`, `_receive_messages` and `add_word_timestamps`
- Deepgram Aura-2 `aura-2-thalia-en` at 24 kHz `linear16`
- Deepgram `/v1/speak` REST, for the speech check and the recorded hold clips

**Spec:** `docs/spec.md`:
- §0, the course changes (PR #9)
- §3, the pipeline
- §9: barge-in keeps only what was spoken
- §10 layer 4

## Decisions this plan makes (review these first)

All of these were prototyped on 2026-10-25 in a scratch clone of `main` (after PR #8), against the real Deepgram and Anthropic APIs, and in Chrome with a recorded caller as the microphone.

| # | Question | Decision | Alternatives |
|---|---|---|---|
| D1 | Which voice? | **`aura-2-thalia-en`**, chosen by Eyal from six samples. Deepgram's voice catalog lists Thalia for customer service and IVR. It's a setting, `VOICE_MODEL`, so another Aura-2 voice needs no code change. | `aura-2-helena-en`, Eyal's other favourite. Andromeda, Asteria, Luna, Apollo. |
| D2 | How do words get timings? | Each sentence is sent as `Speak` and then `Flush`. Words are released as audio arrives, at estimated starts computed from `(len(word)+1) × seconds_per_letter`. The rate starts at 0.06 s and is re-learned from each flushed sentence. On `Flushed`, words not yet released are spread evenly between the last released word and the sentence's real end. **Measured in the prototype:** a 25-word answer revealed progressively over about 9 s, in step with the audio. A barge-in truncated at the spoken word ("…at least 5,000 square — cut off"), and the agent's next reply offered to finish the cut-off answer, because `heard()` kept only the spoken part. | Release a sentence's words only on `Flushed`. Measured: Deepgram streams long sentences at about 2× real time, so a 180-character sentence's words arrived 4 s late in one burst. Plain `DeepgramTTSService`: every sentence is marked spoken as soon as it's synthesized, which breaks all three behaviours. |
| D3 | Barge-in | `on_audio_context_interrupted` clears the sentence queue and drops all audio and `Flushed` messages until Deepgram answers the `Clear` with `Cleared`. Pipecat's own handler still sends the `Clear`. | Trust the active audio context. Late audio from the cancelled turn would then be appended to the new turn. |
| D4 | What happens to ElevenLabs? | **Removed:** the settings, the `elevenlabs` pip extra, the workflow secret and the README lines. Going back is one `git revert` of this branch. | Keep both providers behind a `TTS_PROVIDER` flag. YAGNI: one voice runs at a time, and a flag doubles the evals. |
| D5 | Hold clips | "Let me check that." and "One moment." are regenerated in Thalia (24 kHz mono, by Deepgram `/v1/speak`), so the lead-in matches the answer's voice. | Keep the Matilda clips, which would mix two voices in one reply. |
| D6 | Evals | The speech check (`evals.speech`) synthesizes with the same `VOICE_MODEL` through Deepgram `/v1/speak`. The audio smoke test is unchanged. **Measured on Aura-2:** speech check 7/7. Audio smoke 11/12 over three runs, at 1.07–1.98 s to first audio. The miss was 2.11 s, and the per-stage log attributes it to LLM inference (1.76–1.93 s), not speech (0.28 s). ElevenLabs runs showed the same LLM variance. | — |
| D7 | Not in this plan | A "voice unavailable" notice on the page when the TTS fails. The deploy (Cloud Run, gate, cap) is M6's own plan. | — |

## Global Constraints

- Python 3.12. `uv run ruff check`, `uv run ruff format --check`, `uv run pyright` (strict) and `uv run pytest` all pass before **every** commit.
- No `# type: ignore` and no `Any` leaking out. Use `typing.override` on overridden Pipecat methods. A `cast` is allowed only at Pipecat's untyped `_get_websocket()` boundary.
- No comments that restate code, narrate a change, or mention tickets or TODOs. The one comment above `TimedDeepgramTTSService` is an allowed external-quirk note.
- Conventional Commits with a body explaining why, ending with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3
  ```
- The repo is public: never commit secrets or `.env`. Never log transcripts or exception messages, only type names: the service yields `ErrorFrame(error=f"{self} error: {type(error).__name__}")`.
- Pipecat stays pinned at `==1.14.0`. Only the extras change: `pipecat-ai[deepgram,websocket]==1.14.0`.
- Don't run paid evals inside tasks. The controller runs the speech check and the audio smoke once, in Task 4.
- Branch `aura-voice` from `main` **after PR #9 merges**. One PR. Eyal merges.

## File map

| File | Change |
|---|---|
| `src/vcity/voice/timed_tts.py` | Create `SentencePacer` and `TimedDeepgramTTSService` (Task 1) |
| `tests/voice/test_timed_tts.py` | Create (Task 1) |
| `src/vcity/voice/call.py` | Use `TimedDeepgramTTSService`, and drop `SPEECH_MODEL` (Task 2) |
| `src/vcity/settings.py` | `VoiceSettings`: drop the ElevenLabs fields, add `voice_model` (Task 2) |
| `tests/test_app.py`, `tests/test_settings.py` | Update for the new `VoiceSettings` (Task 2) |
| `src/vcity/voice/audio/let_me_check.wav`, `one_moment.wav` | Thalia recordings (Task 0) |
| `pyproject.toml`, `uv.lock` | Drop the `elevenlabs` extra (Task 2) |
| `evals/speech/__main__.py` | Synthesize through Deepgram (Task 3) |
| `.github/workflows/audio-smoke.yml` | Drop `ELEVENLABS_API_KEY` (Task 3) |
| `README.md`, `docs/spec.md` | The voice: configuration, pipeline, speech check, and spec §0/§3/§14 (Task 3) |

---

### Task 0 (controller, before dispatch): branch, plan, recordings

- [ ] Wait until PR #9 is merged. Then `git switch main && git pull --ff-only && git switch -c aura-voice`.
- [ ] Copy the Thalia hold clips from the prototype over the current ones:
  - from `/private/tmp/claude-1820712540/-Users-eyalelkevity-Genoox-dev-vcity/706c8c0b-b371-4095-a155-02221e146ebb/scratchpad/thalia_clips/let_me_check.wav`
  - and `…/thalia_clips/one_moment.wav`
  - to `src/vcity/voice/audio/`

  They're 24 kHz mono 16-bit, "Let me check that." (1.20 s) and "One moment." (0.76 s), generated with Deepgram `/v1/speak` using `model=aura-2-thalia-en`.
- [ ] Commit the plan and the clips:

```bash
git add docs/superpowers/plans/2026-10-25-aura-voice.md src/vcity/voice/audio/let_me_check.wav src/vcity/voice/audio/one_moment.wav
git commit -m "docs: add the Aura-2 voice plan and record the hold lines in its voice" -m "The ElevenLabs free quota ran out, so the line moves to Deepgram Aura-2 on the existing Deepgram credit. The plan records how word timings are estimated so the page reveal, barge-in truncation and heard history keep working; the hold lines are re-recorded in Thalia so a reply doesn't switch voices mid-sentence." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 1: Timed Deepgram TTS service

**Files:**
- Create: `src/vcity/voice/timed_tts.py`
- Create: `tests/voice/test_timed_tts.py`

**Interfaces:**
- Produces:
  - `vcity.voice.timed_tts.TimedDeepgramTTSService(*, api_key: str, sample_rate: int, voice: str, text_transforms: list[tuple[str, TextTransform]])`
  - `SentencePacer(words: list[str], seconds_per_letter: float)` with `.audio(seconds) -> list[tuple[str, float]]`, `.finish() -> list[tuple[str, float]]` and `.letters`
  - The public test seams `on_audio(bytes)` and `on_control(dict)`

**Notes for the implementer:**
- **The rate.** `self.sample_rate` is 0 until Pipecat sends a `StartFrame`, so the service keeps its own `_bytes_per_second` from the constructor's `sample_rate`.
- **Word text.** Pipecat's word tracker matches timestamps against the *transformed* text it registered: the `speakable()` output, such as "six point twenty point oh twenty". So the words come from `run_tts(text)`, which receives that transformed text. Pipecat maps them back to the written sentence for `spoken_progress`.
- **Overrides.** `push_text_frames=False` is what makes Pipecat use word timestamps instead of pushing whole-sentence text frames. The same flag is set by `ElevenLabsTTSService`.

- [ ] **Step 1: Write the failing tests** in `tests/voice/test_timed_tts.py`

```python
import json
from contextlib import aclosing
from typing import cast, override

import pytest
from pipecat.frames.frames import Frame, TTSStoppedFrame
from pipecat.utils.context.word_timestamp_tracker import WordTimestamp
from websockets.asyncio.client import ClientConnection

from vcity.voice.timed_tts import SentencePacer, TimedDeepgramTTSService

RATE = 24000
ONE_SECOND = bytes(2 * RATE)


def test_words_are_released_once_the_audio_reaches_them() -> None:
    pacer = SentencePacer(["Hens", "are", "fine."], seconds_per_letter=0.1)
    assert pacer.audio(0.1) == [("Hens", 0.0)]
    assert pacer.audio(0.5) == [("are", pytest.approx(0.5))]
    assert pacer.audio(0.4) == [("fine.", pytest.approx(0.9))]


def test_finishing_fits_unreleased_words_into_the_real_audio_length() -> None:
    pacer = SentencePacer(["Hens", "are", "allowed."], seconds_per_letter=1.0)
    pacer.audio(0.6)
    assert pacer.finish() == [("are", pytest.approx(0.2)), ("allowed.", pytest.approx(0.4))]


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Service(TimedDeepgramTTSService):
    def __init__(self) -> None:
        super().__init__(
            api_key="k", sample_rate=RATE, voice="aura-2-thalia-en", text_transforms=[]
        )
        self.socket = _Socket()
        self.words: list[tuple[str, float]] = []
        self.queued: list[Frame] = []
        self.removed: list[str | None] = []

    @override
    def _socket(self) -> ClientConnection:
        return cast("ClientConnection", self.socket)

    @override
    def get_active_audio_context_id(self) -> str | None:
        return "turn"

    @override
    async def append_to_audio_context(
        self, context_id: str | None, frame: Frame | WordTimestamp | None
    ) -> None:
        if isinstance(frame, Frame):
            self.queued.append(frame)

    @override
    async def remove_audio_context(self, context_id: str | None) -> None:
        self.removed.append(context_id)

    @override
    async def add_word_timestamps(
        self,
        word_times: list[tuple[str, float]],
        context_id: str | None = None,
        includes_inter_frame_spaces: bool | None = None,
        pre_merge_tokens: bool = False,
    ) -> None:
        self.words += word_times


async def _speak(service: _Service, text: str) -> None:
    async with aclosing(service.run_tts(text, "turn")) as frames:
        async for _ in frames:
            pass


async def test_each_sentence_is_flushed_so_its_audio_length_is_known() -> None:
    service = _Service()
    await _speak(service, "Hens are fine.")
    assert service.socket.sent == [{"type": "Speak", "text": "Hens are fine."}, {"type": "Flush"}]


async def test_the_second_sentence_is_timed_after_the_first() -> None:
    service = _Service()
    await _speak(service, "Yes.")
    await _speak(service, "Hens are fine.")
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    assert [word for word, _ in service.words] == ["Yes.", "Hens", "are", "fine."]
    assert service.words[1][1] == pytest.approx(1.0)


async def test_the_turn_ends_when_its_last_sentence_is_flushed() -> None:
    service = _Service()
    await _speak(service, "Yes.")
    await service.flush_audio("turn")
    assert service.removed == []
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    assert service.removed == ["turn"]
    assert isinstance(service.queued[-1], TTSStoppedFrame)


async def test_audio_after_an_interruption_is_dropped_until_deepgram_clears() -> None:
    service = _Service()
    await _speak(service, "Hens are fine.")
    await service.on_audio_context_interrupted("turn")
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    assert service.words == []
    await service.on_control({"type": "Cleared"})
    await _speak(service, "Okay.")
    await service.on_audio(ONE_SECOND)
    assert service.words == [("Okay.", 0.0)]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/voice/test_timed_tts.py -q`
Expected: collection error, `No module named 'vcity.voice.timed_tts'`.

- [ ] **Step 3: Implement `src/vcity/voice/timed_tts.py`**

```python
import json
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import cast, override

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.utils.text.transforms.text_transform import TextTransform
from websockets.asyncio.client import ClientConnection

BYTES_PER_SAMPLE = 2
FIRST_SECONDS_PER_LETTER = 0.06

type WordTimes = list[tuple[str, float]]


@dataclass
class SentencePacer:
    words: list[str]
    seconds_per_letter: float
    heard_seconds: float = 0.0
    released: int = 0
    starts: list[float] = field(default_factory=list[float])

    def __post_init__(self) -> None:
        elapsed = 0.0
        for word in self.words:
            self.starts.append(elapsed)
            elapsed += (len(word) + 1) * self.seconds_per_letter

    @property
    def letters(self) -> int:
        return sum(len(word) + 1 for word in self.words)

    def audio(self, seconds: float) -> WordTimes:
        self.heard_seconds += seconds
        due = self.released
        while due < len(self.words) and self.starts[due] <= self.heard_seconds:
            due += 1
        return self._release(due - self.released)

    def finish(self) -> WordTimes:
        left = range(self.released, len(self.words))
        if left and self.starts[-1] >= self.heard_seconds:
            last = self.starts[self.released - 1] if self.released else 0.0
            step = (self.heard_seconds - last) / (len(left) + 1)
            for k, i in enumerate(left, start=1):
                self.starts[i] = last + step * k
        return self._release(len(left))

    def _release(self, count: int) -> WordTimes:
        out = [(self.words[i], self.starts[i]) for i in range(self.released, self.released + count)]
        self.released += count
        return out


# Deepgram's speak socket sends no word timings. Flushing each sentence on its own marks where its
# audio ends, and its words are spread over that audio as it arrives, so Pipecat can follow
# playback word by word.
class TimedDeepgramTTSService(DeepgramTTSService):
    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int,
        voice: str,
        text_transforms: list[tuple[str, TextTransform]],
    ) -> None:
        super().__init__(
            api_key=api_key,
            sample_rate=sample_rate,
            settings=DeepgramTTSService.Settings(voice=voice),
            text_transforms=text_transforms,
            push_text_frames=False,
        )
        self._bytes_per_second = BYTES_PER_SAMPLE * sample_rate
        self._sentences: deque[SentencePacer] = deque()
        self._seconds_per_letter = FIRST_SECONDS_PER_LETTER
        self._offset = 0.0
        self._closing = False
        self._clearing = False

    @override
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None]:
        if not self._sentences and not self._closing:
            self._offset = 0.0
        self._sentences.append(SentencePacer(text.split(), self._seconds_per_letter))
        try:
            websocket = self._socket()
            await websocket.send(json.dumps({"type": "Speak", "text": text}))
            await websocket.send(json.dumps({"type": "Flush"}))
        except Exception as error:  # noqa: BLE001
            yield ErrorFrame(error=f"{self} error: {type(error).__name__}")
        yield None

    @override
    async def flush_audio(self, context_id: str | None = None) -> None:
        self._closing = True
        if not self._sentences:
            await self._finish_context()

    @override
    async def on_audio_context_interrupted(self, context_id: str) -> None:
        self._sentences.clear()
        self._closing = False
        self._clearing = True
        await super().on_audio_context_interrupted(context_id)

    @override
    async def _receive_messages(self) -> None:
        async for message in self._socket():
            if isinstance(message, bytes):
                await self.on_audio(message)
            else:
                await self.on_control(json.loads(message))

    async def on_audio(self, audio: bytes) -> None:
        if self._clearing or not self._sentences:
            return
        context_id = self.get_active_audio_context_id()
        await self.append_to_audio_context(
            context_id, TTSAudioRawFrame(audio, self.sample_rate, 1, context_id=context_id)
        )
        await self._time_words(self._sentences[0].audio(len(audio) / self._bytes_per_second))

    async def on_control(self, message: dict[str, object]) -> None:
        match message.get("type"):
            case "Cleared":
                self._clearing = False
            case "Flushed" if not self._clearing and self._sentences:
                sentence = self._sentences.popleft()
                await self._time_words(sentence.finish())
                if sentence.letters:
                    self._seconds_per_letter = sentence.heard_seconds / sentence.letters
                self._offset += sentence.heard_seconds
                if self._closing and not self._sentences:
                    await self._finish_context()
            case _:
                pass

    def _socket(self) -> ClientConnection:
        return cast("ClientConnection", self._get_websocket())

    async def _time_words(self, timed: WordTimes) -> None:
        if timed:
            await self.add_word_timestamps(
                [(word, self._offset + start) for word, start in timed],
                self.get_active_audio_context_id(),
            )

    async def _finish_context(self) -> None:
        self._closing = False
        context_id = self.get_active_audio_context_id()
        if context_id is None:
            return
        await self.append_to_audio_context(context_id, TTSStoppedFrame(context_id=context_id))
        await self.remove_audio_context(context_id)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/voice/test_timed_tts.py -q`
Expected: `6 passed`

- [ ] **Step 5: All checks, then commit**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`

```bash
git add src/vcity/voice/timed_tts.py tests/voice/test_timed_tts.py
git commit -m "feat: estimate word timings for Deepgram's voice" -m "Deepgram's speak socket returns audio but no word timings, and without them Pipecat marks every sentence spoken the moment it is synthesized: the page can't reveal words as they are said, a barge-in can't be placed, and the history keeps words the caller never heard. Flushing each sentence marks where its audio ends, and its words are released as that audio arrives at a speaking rate learned from earlier sentences, so Pipecat's playback-synced word handling works as it did with ElevenLabs." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 2: Speak with Aura-2

**Files:**
- Modify: `src/vcity/voice/call.py`, `src/vcity/settings.py`
- Modify: `tests/test_app.py`, `tests/test_settings.py`
- Modify: `pyproject.toml`, `uv.lock`

**Interfaces:**
- Consumes: `TimedDeepgramTTSService` (Task 1)
- Produces: `VoiceSettings.voice_model: str = "aura-2-thalia-en"` (env `VOICE_MODEL`), which Task 3's speech check uses. `VoiceSettings` no longer has `elevenlabs_api_key` or `elevenlabs_voice_id`, and `vcity.voice.call.SPEECH_MODEL` is removed.

- [ ] **Step 1: Update the failing tests first**

In `tests/test_settings.py`, replace `test_voice_settings_mask_both_keys` with:

```python
def test_voice_settings_mask_the_key_and_default_the_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.delenv("VOICE_MODEL", raising=False)
    settings = VoiceSettings.from_env()
    assert settings.deepgram_api_key.get_secret_value() == "dg-test"
    assert "dg-test" not in repr(settings)
    assert settings.voice_model == "aura-2-thalia-en"
    assert settings.still_working_after == 1.5
```

In `tests/test_app.py`, replace the `VOICE = VoiceSettings(...)` construction with:

```python
VOICE = VoiceSettings(deepgram_api_key=SecretStr("d"), voice_model="aura-2-thalia-en")
```

Run: `uv run pytest tests/test_settings.py tests/test_app.py -q`
Expected: FAIL, because `voice_model` doesn't exist.

- [ ] **Step 2: Settings.** In `src/vcity/settings.py` `VoiceSettings`, replace

```python
    elevenlabs_api_key: SecretStr
    elevenlabs_voice_id: str = "XrExE9yKIg1WjnnlVkGX"
```

with

```python
    voice_model: str = "aura-2-thalia-en"
```

- [ ] **Step 3: Pipeline.** In `src/vcity/voice/call.py`:
  - remove the `from pipecat.services.elevenlabs.tts import ElevenLabsTTSService` import and the `SPEECH_MODEL = "eleven_flash_v2_5"` constant
  - add `from vcity.voice.timed_tts import TimedDeepgramTTSService` after the `speakable` import
  - replace the `tts = ElevenLabsTTSService(...)` construction with:

```python
    tts = TimedDeepgramTTSService(
        api_key=voice.deepgram_api_key.get_secret_value(),
        sample_rate=SAMPLE_RATE,
        voice=voice.voice_model,
        text_transforms=[("*", _speakable)],
    )
```

- [ ] **Step 4: Dependency.** In `pyproject.toml`, change `"pipecat-ai[deepgram,elevenlabs,websocket]==1.14.0"` to `"pipecat-ai[deepgram,websocket]==1.14.0"`. Then run `uv lock` and `uv sync`.

`evals/speech/__main__.py` still imports `SPEECH_MODEL` until Task 3. Make the full suite pass in this task by applying Task 3 Step 1's `round_trip` change too. Pyright would otherwise fail on the import, and every commit must pass the checks.

- [ ] **Step 5: All checks, then commit**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`
Expected: all pass. `grep -rn "elevenlabs\|ELEVENLABS" src tests evals pyproject.toml` prints nothing.

```bash
git add src/vcity/voice/call.py src/vcity/settings.py tests/test_app.py tests/test_settings.py pyproject.toml uv.lock evals/speech/__main__.py
git commit -m "feat: speak with Deepgram Aura-2 instead of ElevenLabs" -m "The ElevenLabs free quota ran out during development, and Aura-2 runs on the Deepgram credit the line already uses for transcription at a fraction of the cost. The voice is a setting (VOICE_MODEL, default Thalia), the timed service keeps word-level playback tracking, and the ElevenLabs dependency, settings and voice ID go away; returning to it is a revert." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 3: Speech check, workflow and docs on the new voice

**Files:**
- Modify: `evals/speech/__main__.py` (if not already done in Task 2)
- Modify: `.github/workflows/audio-smoke.yml`, `README.md`, `docs/spec.md`

- [ ] **Step 1: Speech check through Deepgram.** In `evals/speech/__main__.py`:
  - remove `from vcity.voice.call import SPEECH_MODEL`
  - replace the ElevenLabs request in `round_trip` with:

```python
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
```

- [ ] **Step 2: Workflow.** In `.github/workflows/audio-smoke.yml`, delete the line `ELEVENLABS_API_KEY: ${{ secrets.ELEVENLABS_API_KEY }}`.

- [ ] **Step 3: README.**
  - In "Configuration", replace these two lines (the ElevenLabs key line inside the code block, and the `ELEVENLABS_VOICE_ID` paragraph after it):

```bash
ELEVENLABS_API_KEY=...  # text to speech; needs the text-to-speech and voices-read permissions
```

```markdown
`ELEVENLABS_VOICE_ID` picks the voice. It defaults to a premade voice, because the free ElevenLabs plan can't use library voices through the API.
```

    with the `DEEPGRAM_API_KEY` comment becoming `# speech to text and text to speech`, and this paragraph after the block:

```markdown
`VOICE_MODEL` picks the Deepgram Aura-2 voice. It defaults to `aura-2-thalia-en`.
```

  - In "The voice line", in the **Pipeline** bullet, replace "and ElevenLabs Flash speaks it." with "and Deepgram Aura-2 speaks it, one sentence at a time, with word timings estimated from each sentence's audio, so the page and barge-in follow the words actually spoken."
  - In "Audio smoke test", replace "It needs the repo secrets `DEEPGRAM_API_KEY` and `ELEVENLABS_API_KEY`." with "It needs the repo secret `DEEPGRAM_API_KEY`."
  - In "Speech check", replace "ElevenLabs voice" with "Deepgram voice".

- [ ] **Step 4: Spec.** In `docs/spec.md`:
  - In the §0 table, replace the third row's "Now" cell `**Deepgram Aura-2, under evaluation**` with `**Deepgram Aura-2 (Thalia)**, with word timings estimated per sentence (see the voice plan)`.
  - In the §3 diagram, replace `TTS (ElevenLabs Flash → Deepgram Aura-2)` with `Deepgram Aura-2 TTS`.
  - In the §14 ElevenLabs row, replace "Deepgram Aura-2 is under evaluation, with ElevenLabs Starter as the paid fallback" with "Moved to Deepgram Aura-2; ElevenLabs Starter is the paid fallback".

- [ ] **Step 5: All checks, then commit**

Run: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q`. Then confirm `grep -rn -i elevenlabs README.md .github src evals` prints nothing.

```bash
git add evals/speech/__main__.py .github/workflows/audio-smoke.yml README.md docs/spec.md
git commit -m "docs: describe the Aura-2 voice and drop the ElevenLabs secret" -m "The speech check must test the voice the call uses, so it synthesizes through Deepgram with the same VOICE_MODEL; the audio smoke workflow no longer needs an ElevenLabs key, and the README and spec now name the voice and how its word timings are estimated." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HtJN2ZQeL7rWj21UEbMRd3"
```

---

### Task 4 (controller): live verification, then the PR

- [ ] Start the branch server on localhost: `PORT=8093 uv run python -m vcity`.
- [ ] `uv run python -m evals.speech`. Expected: 7/7.
- [ ] `uv run python -m evals.audio --url ws://127.0.0.1:8093/ws/browser`. Expected: 4/4, first audio ≤ 2 s. A single LLM-slow miss gets one rerun, and the result is reported honestly.
- [ ] Chrome with the fake microphone (`.superpowers/uiprobe/fake_mic.js`). Expected:
  - the City bubble reveals progressively, with word counts rising second by second
  - the barged-in sentence shows "— cut off" at a mid-sentence word
  - no bubble for "Hi."
- [ ] Eyal listens live at `http://127.0.0.1:8093/call`.
- [ ] Final whole-branch review on the most capable model, one fix wave, then the PR with the 🤖 footer and the session link.
- [ ] Update the project memory carry-over: voice is now Aura-2 Thalia, and the `ELEVENLABS_API_KEY` repo secret is unused.
