# vcity

A voice agent that answers the phone for the City of Corona, CA. It answers questions from the municipal code and the city's website and news, routes callers to the right department during business hours, and files tickets when the office is closed.

- Design: [`docs/spec.md`](docs/spec.md)
- Write-up (decisions, results, what isn't working): [`WRITEUP.md`](WRITEUP.md)

## Development

```bash
uv sync
uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest
```

Live-API tests are skipped by default. Run them with `uv run pytest -m live`. They need `.env`.

## Configuration

Copy these into `.env`, which is gitignored:

```bash
VOYAGE_API_KEY=...      # embeddings
ANTHROPIC_API_KEY=...   # relevance classifier
GAP_LOG_SALT=...        # any random string; hashes caller IDs in the gap log
LINEAR_API_KEY=...      # tickets
DEEPGRAM_API_KEY=...    # speech to text and text to speech
```

`VOICE_MODEL` picks the Deepgram Aura-2 voice. It defaults to `aura-2-thalia-en`.

Settings for the public line:

```bash
ACCESS_CODE=...         # callers must enter it; unset means no code is needed
MAX_CALL_SECONDS=600    # a call ends after this long
RECYCLE_RSS_MB=1400     # after a call, restart the server if it holds more memory than this
```

## Knowledge index

`search_city` answers from one SQLite file, built from four sources:
- the municipal code snapshot in `data/raw/code/`
- the English pages of coronaca.gov
- city news
- the city's events feed

```bash
uv run python -m vcity.knowledge.ingest      # writes build/index.sqlite (embeddings cached in build/)
uv run python -m evals.retrieval             # hit@3, MRR and the abstention threshold sweep
```

CI downloads the published index from the `knowledge-index` release. CI reads Voyage's key from the repo secret VOYAGE_API_KEY. The manual **content-refresh** workflow rebuilds the index, gates it with the retrieval eval, and republishes it.

### Refreshing the municipal code snapshot

American Legal blocks scripted access, so the snapshot is taken from a real browser session:

1. Open https://codelibrary.amlegal.com/codes/corona/latest/overview in Chrome and open the DevTools console.
2. Paste `tools/amlegal_snapshot.js` and run it with a download wrapper. It takes about 25 minutes:
   ```js
   const snapshot = await (/* paste tools/amlegal_snapshot.js here */)();
   const link = document.createElement("a");
   link.href = URL.createObjectURL(new Blob([JSON.stringify(snapshot)]));
   link.download = "corona-municipal-code.json";
   link.click();
   ```
3. Compress it and replace the committed copy:
   ```bash
   gzip -9 corona-municipal-code.json && mv corona-municipal-code.json.gz data/raw/code/
   ```

## Tickets

Tickets go to the Linear team **Corona 311** (key `COR`). Setup is one idempotent command. It creates the team, a label per department, and the `gap` label:

```bash
uv run python -m vcity.tickets.linear_setup
```

- **References:** every service ticket gets a six-digit reference that the caller can quote. It's in the ticket title.
- **Outbox:** if Linear doesn't answer within 3 seconds, or errors, on two attempts, the ticket is queued in a local SQLite outbox. The caller still gets the reference. A background sender started by the app retries every minute. The outbox lives on the container's in-memory disk, so it is lost when the instance goes away. Pending tickets get one more try, within 6 s, at shutdown.
- **Gap tickets:**
  - Questions the city's records can't answer are clustered by meaning into `gap` tickets.
  - Each repeat adds a "+1" comment and refreshes the ticket's stats.
  - Priority follows the asks in the last 7 days: at least 1 is low, at least 3 is medium, and at least 10 is high.

## The agent (text)

The agent is Claude Haiku 5.5 with adaptive thinking and three tools: `search_city`, `route_request` (transfer or ticket, chosen by office hours) and `emergency_guidance`. The voice line runs the same prompt, tools and conversation loop.

```bash
uv run python -m vcity.agent.chat --tools      # talk to it; tickets go to an in-memory mock
uv run python -m vcity.agent.chat --linear     # file real tickets in Linear
```

## Scenario eval

```bash
uv run python -m evals.scenarios                       # 42 scripted calls × 3 runs, judge and simulated callers
uv run python -m evals.scenarios --model claude-sonnet-5-5 --no-simulate
```

- **Gate:** every scenario must pass its exact checks in all 3 runs, except scenarios marked `known_issue`. The checks cover tools called, department, outcome, ticket reason, and what was said.
- **Report-only:** a Claude Sonnet 5.5 judge scores each call against a reference answer, and five LLM-played callers try to reach a goal.
- **Report:** everything lands in `reports/scenarios.html`, together with the retrieval metrics and the threshold plot.
- **CI:** the **scenarios** workflow runs on every merge to `main`, before the deploy, and on demand. It needs the repo secrets `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`.

## The voice line

```bash
uv run python -m vcity      # http://localhost:8080/call
```

The server listens on `127.0.0.1:8080`. Set `HOST` and `PORT` to change that (a container sets `HOST=0.0.0.0`).

- **Page:** `/call` streams your microphone to the server over a WebSocket, with Pipecat's browser client. The lemon shows whether the line is listening, thinking or speaking. The transcript shows each sentence once, as it's spoken, and marks the ones you cut off.
- **Pipeline, one per call:** Silero VAD and Smart Turn v3 decide when you've finished, Deepgram Nova-3 transcribes, the agent streams its reply, and Deepgram Aura-2 speaks it. Each sentence is flushed on its own while Deepgram's flush budget allows, and grouped with the next ones once it's spent, with word timings estimated from each flush's audio, so the page and barge-in follow the words actually spoken.
- **Interruptions:** two or more words over the agent stop playback, cancel the reply, and keep only what you actually heard in the conversation, marked as cut off so the agent answers you first and then repeats anything you missed that still matters. A single word ("okay", a cough, a TV in the room) doesn't interrupt.
- **Waiting:** when a tool starts, a recorded "Let me check that." plays at once; "One moment." follows if the tool is still running 1.5 s later.
- **Transfers:** in the browser a transfer is shown on screen and the call ends. On a Twilio number the call is dialed through instead (see Phone line).
- **Latency:** every reply logs its per-stage breakdown (end of speech, transcription, turn detection, LLM, speech synthesis) and the page shows the total.

## Audio smoke test

```bash
uv run python -m vcity &    # the server must be running
uv run python -m evals.audio
```

Against a deployed service, pass its URL and the access code:

```bash
ACCESS_CODE=... uv run python -m evals.audio --base https://<service-url>
```

Plays the recorded callers in `evals/audio/callers/` into the voice line in real time. Each passes if the agent says what's expected (or transfers), its first audio arrives within 2 s of the caller going quiet, and nothing cuts it off. The `aside` case says "Okay." two seconds into the answer to prove one word doesn't interrupt. The **audio-smoke** workflow runs it on demand. It needs the repo secret `DEEPGRAM_API_KEY`.

## Speech check

```bash
uv run python -m evals.speech
```

Sends the phrases in `evals/speech/phrases.yaml` through the same number-to-words step and Deepgram voice the call uses, transcribes the audio with Deepgram, and checks that section numbers, amounts, times and phone numbers come back as the expected words. The **audio-smoke** workflow runs it too.

## Deploy

The line runs on Cloud Run in `us-west1`: one instance with 2 GiB, one call at a time, calls capped at 10 minutes. Memory grows about 30 MB per call. After a call, if the server holds more than 1400 MB and the line is free, it stops and Cloud Run starts a fresh one.

Callers need the access code. The page asks for it, and 10 wrong codes within 10 minutes lock everyone out for 60 s.

CI on `main`: checks, retrieval eval, scenarios, then deploy. Pull requests stop after the retrieval eval. The content-refresh workflow redeploys after it rebuilds the index. The deploy authenticates with Workload Identity Federation, so no key is stored.

One-time setup, with `gcloud` signed in to the project:

```bash
bash tools/gcp_setup.sh      # APIs, registry, service accounts, federation, secrets
bash tools/gcp_secrets.sh    # loads the keys, the Twilio token and the transfer number from .env, prompts for the access code
```

All nine secrets (ANTHROPIC_API_KEY, VOYAGE_API_KEY, DEEPGRAM_API_KEY, LINEAR_API_KEY, GAP_LOG_SALT, ACCESS_CODE, TWILIO_AUTH_TOKEN, TRANSFER_TO, TWILIO_WEBHOOK_KEY) need a version before the first deploy, because the deploy mounts each one.

- **URL:** in the deploy step's run summary, or:
  ```bash
  gcloud run services describe vcity --region us-west1 --project vcity-512721 --format 'value(status.url)'
  ```
- **Rotate the code:**
  ```bash
  printf %s "$NEW" | gcloud secrets versions add ACCESS_CODE --project vcity-512721 --data-file=-
  gcloud run services update vcity --region us-west1 --project vcity-512721 \
    --update-env-vars CODE_ROTATED_AT="$(date +%s)"
  ```
  Rerunning `bash tools/gcp_secrets.sh` and the latest deploy also works, but it re-versions every secret from `.env`.
- **Shut down:**
  ```bash
  gcloud run services delete vcity --region us-west1 --project vcity-512721
  ```

### Phone line (Twilio)

The phone line is built and tested but not live. Twilio's free trial gives a Twilio-owned "Try out Voice" number, and that number blocks `<Stream>` and `<Dial><Number>` ([Twilio's trial TwiML rules](https://www.twilio.com/docs/usage/trials/try-out-voice)): a call hears Twilio's trial message and hangs up. A live line needs an upgraded account (about $20) and a number of its own, with "A call comes in" pointed at the keyed webhook below. The browser `/call` is the demo.

A Twilio number reaches the same agent as `/call`. The routes mount when `TWILIO_AUTH_TOKEN` or `TWILIO_WEBHOOK_KEY` is set. A webhook is admitted by a matching `?key=` or, for a number on our own account, by `X-Twilio-Signature`. Every follow-up URL the server hands Twilio carries the key. A transfer dials `TRANSFER_TO`; the person who answers presses 1 to accept, and if nobody does, the caller hears a ticket reference.

- **Webhook key:** create it once, without printing it:
  ```bash
  openssl rand -hex 24 | tr -d '\n' | gcloud secrets versions add TWILIO_WEBHOOK_KEY --data-file=- --project vcity-512721
  ```
  (`tools/gcp_setup.sh` creates the empty secret.) Rotate by adding a new version, redeploying, and updating the Twilio field.
- **Webhook:** set the number's "A call comes in" webhook to the URL with its key, method POST. This copies it to the clipboard:
  ```bash
  printf 'https://vcity-dbykunzueq-uw.a.run.app/twilio/voice?key=%s' "$(gcloud secrets versions access latest --secret TWILIO_WEBHOOK_KEY --project vcity-512721)" | pbcopy
  ```
  Query strings appear in Cloud Run's request logs, which are private to the project.
- **Two phones:** the caller must not be the `TRANSFER_TO` phone. A transfer to the phone placing the call finds it busy and falls back to a ticket.

#### Office hours on the phone

The phone uses Corona's real clock by default, so during an Israeli workday every office is closed. `PHONE_HOURS` pretends instead, using the same times as the web page's switch: `open` is 2:00 PM on the nearest Monday to Thursday, and `closed` is 8:00 PM today.

```bash
# Offices open, so transfers ring TRANSFER_TO
gcloud run services update vcity --region us-west1 --project vcity-512721 --update-env-vars PHONE_HOURS=open

# Offices closed, so requests are filed for the next opening
gcloud run services update vcity --region us-west1 --project vcity-512721 --update-env-vars PHONE_HOURS=closed

# Back to the real clock
gcloud run services update vcity --region us-west1 --project vcity-512721 --remove-env-vars PHONE_HOURS
```

Each command starts a new revision in about 30 seconds and drops a call in progress, so switch between calls. The value persists until it is changed again.
