# vcity: Corona, CA voice agent: spec

This spec covers the Threefold developer take-home: a voice agent for a US city. It lists only the decisions we've locked.

## 0. Course changes (2026-10-25)

Evidence during the build reversed three locked decisions. The sections below are updated to match.

| Was | Now | Why |
|---|---|---|
| A Twilio phone line is the main surface. The browser page is a fallback | **The browser call at `/call` is the delivered surface.** The phone line follows as a Twilio trial number (§8) | The task asks for "a live link where they can try the agent themselves" and accepts a mock phone number for routing. Twilio must reach a public URL. Exposing a development laptop through a tunnel isn't acceptable. A trial number would also only accept a handful of verified callers. |
| Render free web service, never paid | **Google Cloud Run** (§11) | Render free gives 0.1 CPU. Measured locally under the same limits, the voice pipeline never spoke at 0.1 CPU, missed the 2 s ceiling at 0.25, and passed at 0.5. Vercel and Netlify run serverless functions that can't hold a voice WebSocket for a whole call. Cloud Run gives a full vCPU, WebSockets, and a free monthly allowance that covers a demo. |
| ElevenLabs Flash for speech | **Deepgram Aura-2 (Thalia)**, with word timings estimated per sentence (see the voice plan) | The ElevenLabs free plan (10,000 characters a month) ran out during development. Aura-2 bills the Deepgram credit the line already uses for speech-to-text, at $0.030 per 1,000 characters. If the voice isn't good enough, ElevenLabs Starter is the paid fallback. |

## 1. Scope

All six asks are delivered. Each one has real decision logic and a test behind it. Only external side effects at the edges are mocked or reduced.

| Ask | Real | Mocked or reduced |
|---|---|---|
| 1. Municipal code + website | Hybrid retrieval with citations, and "I don't know" below the relevance gate | Only the code chapters that common calls need |
| 2. Current events | News and events from the city site, with the "as of" date spoken | A crawl on manual trigger, not live search |
| 3. Ticketing | Linear API behind an adapter | A mock adapter for evals |
| 4. Routing | Department classification, business-hours decision, and the transfer itself | The browser shows the transfer on screen with the department's number instead of dialling; the Twilio trial phone line dials the transfer (§8) |
| 5. Business hours | A deterministic pure function, unit tested | Nothing |
| 6. Evals | Four layers, gating deploys | Nothing |

**Stretch goals.** These are built only after the core is done:
- An SMS with the cited link.
- Live search limited to city domains, as a low-score fallback.
- Automatically filed knowledge-gap tickets.
- A polished HTML writeup page.

**Out of scope:**
- Spanish.
- Detecting the caller's emotion or tone.
- Multi-party calls.
- A voicemail product.

## 2. City facts

- **City:** Corona, CA. Time zone `America/Los_Angeles`.
- **City Hall hours:** Mon–Thu 07:00–18:00, closed Friday. The site's footer says so on every department page.
- **Animal Services shelter:** Mon–Sat 11:00–17:30.
- **Municipal code:** published on American Legal, codelibrary.amlegal.com/codes/corona.
  - It has 19 titles, and each chapter is one page.
  - Section headings are `div.Section.rbox`, each with an anchor like `#JD_6.20.010`.
  - The pages sit behind a bot challenge, so we take them as a manual snapshot.
- **City site:** coronaca.gov, built on Webflow.
  - `sitemap.xml` lists 1,406 URLs. We crawl the English ones only and skip `/es`.
  - News list pages are server-rendered. Each article has an `h1` title, a date and the body.
  - Events are at `/events`.
- **Holidays:** US federal plus California holidays, from the `holidays` package (`US`, `subdiv="CA"`). The city publishes no list of its own, so the writeup records this as an assumption.

## 3. Architecture

```
browser ──▶ /call page ─────(audio WS)──────────┐
caller ──▶ Twilio number ──(Media Streams WS)──┤   (Twilio trial, §8)
                                                ▼
              Pipecat pipeline (one per call, asyncio)
  transport in → Silero VAD + Smart Turn → Deepgram Nova-3 STT
  → context aggregator → Claude (tools) → Deepgram Aura-2 TTS → transport out
                                                │ tool calls
                                                ▼
        domain (pure)  ◀── knowledge · tickets · telephony (adapters)
```

**Dependency rule (ports and adapters).** `domain/` imports nothing from the other packages. The adapters depend on the domain through `Protocol` ports, never the other way around.

### 3.1 Repo layout

```
src/vcity/
  domain/      departments, hours (decide, next_open), decision types
  knowledge/   code parser, site crawler, chunker, SQLite index, search_city, relevance classifier, gap log
  tickets/     TicketSink Protocol, LinearSink, MockSink, Outbox
  telephony/   Twilio webhooks, transfer with whisper + press-1, dial-status callback
  voice/       Pipecat pipeline, tool wiring, system prompt, latency instrumentation
  app.py       FastAPI app: browser-call WS and page, /health (Twilio routes live in telephony/)
data/          departments.yaml · raw/code/*.html · raw/code/sources.json
evals/         scenarios/*.yaml · retrieval/*.yaml · runner · judge · report
tests/         unit tests
docs/          spec.md · implementation plans
WRITEUP.md · README.md · CLAUDE.md · Dockerfile · pyproject.toml · .github/workflows/
```

## 4. Domain

```python
class Department(StrEnum): ...   # the 9 departments in §4.1

@dataclass(frozen=True)
class Transfer:
    number: str

@dataclass(frozen=True)
class Ticket:
    reason: TicketReason          # after_hours | holiday | closing_soon | transfer_failed | caller_requested
    next_open: datetime | None

type RoutingDecision = Transfer | Ticket

def decide(dept: DepartmentInfo, now: datetime, policy: HoursPolicy) -> RoutingDecision: ...
def next_open(dept: DepartmentInfo, now: datetime, policy: HoursPolicy) -> datetime: ...
```

- **Time windows** are half-open: `open <= t < close`. All checks run in `America/Los_Angeles`.
- **`HoursPolicy`** holds `closing_soon = timedelta(minutes=10)` and the holiday calendar.
- **No hidden clock.** The current time is always passed in, and `domain/` never reads the system clock.

### 4.1 `data/departments.yaml`

Each department entry has these fields:
- `id`
- `name`
- `description`: what callers come to it for, which the model uses to classify
- `public_phone`: the real number, which the agent may say out loud
- `transfer_to`: a verified demo number in E.164 format
- `hours`: weekday mapped to `[open, close]`
- `source_url`

The 9 departments:
1. Public Works
2. Maintenance Services
3. Utilities
4. Code Compliance
5. Animal Services
6. Building/Planning
7. City Clerk
8. Finance
9. Police (non-emergency)

Phone numbers come from each department's page and are checked by hand before they're committed.

## 5. Knowledge (asks 1 and 2)

### 5.1 Ingest

The ingest runs offline, in CI, on a manual trigger. It produces `index.sqlite`, which is baked into the image.

- **Code.** A person saves the needed chapters from American Legal into `data/raw/code/`, using the site's Download option or by saving the page.
  - `sources.json` records each file's URL, the sections it covers, and the date it was saved.
  - The parser emits one record per section: `{section, title, text, url, saved_on}`.
  - The parsed sections must match the chapter's table of contents exactly.
- **Site.** We walk `sitemap.xml`, English only. Department, service and parks pages are split at their headings. News articles keep their title and date. Events keep their date.
- **Chunking.** Chunks follow the document's structure.
  - One code section becomes one chunk. Long sections split at their lettered subsections.
  - Every chunk is prefixed with a breadcrumb, for example `Corona Municipal Code › Title 6 Animals › Ch. 6.20 … › 6.20.020 …`.
- **Index.** One SQLite file holds:
  - FTS5 for BM25 keyword search
  - `sqlite-vec` for vectors
  - Voyage embeddings

### 5.2 `search_city` tool

- **Input:** `query: str` and `kind: code | site | news | events | None`.
- **Steps:**
  1. A relevance classifier labels the query `city`, `not_city` or `unclear`. It uses a small, fast model with structured output, and `unclear` counts as `city`. A `not_city` label returns `off_topic`.
  2. Hybrid retrieval runs BM25 and vector search, and merges them with reciprocal rank fusion.
  3. For `news`, recent items rank higher. For `events`, only upcoming ones are returned.
  4. **The relevance gate:** if the top chunk's cosine similarity is below τ, the tool returns `no_match`. τ is chosen from the retrieval eval.
- **Output:** a `status` of `match`, `no_match` or `off_topic`, plus chunks `[{cite_id, breadcrumb, text, url, date}]`.
- **Gap log.** Every `no_match` or abstention is logged to the gap log, with personal details stripped and the caller ID hashed.
- **Grounding: speak and verify.** Answers stream straight to speech. In parallel, a checker confirms that every cited `cite_id` was among the retrieved chunks, and logs any violation with the call ID.
- **Speaking citations.** The agent names the source out loud ("Under Corona Municipal Code section 6.20.020…") and gives dates for news and events.

## 6. Agent tools and conversation

**Tools.**
- **`search_city`:** see §5.2.
- **`route_request(department, summary, location?, prefer_ticket?)`:** runs `decide`, then either transfers the call or files a ticket. It returns the outcome together with `next_open`.
- **`emergency_guidance()`:** returns the fixed script "hang up and dial 911". The agent never transfers or files a ticket for an emergency.
- **`send_link_sms(cite_id)`:** dropped with the phone line (§0).

**Rules in the prompt.**
- `department` is an enum in the tool schema, so the model can only choose a real department.
- If the model is unsure of the department, it asks the caller one clarifying question.
- Before filing a ticket, the agent reads the summary and location back to the caller.
- Answers stay short and speakable: no URLs and no lists.

**Waiting.**
- The prompt has the model say a one-line lead-in before slow tools.
- If a tool is still running after 1.5 s, the code plays a pre-recorded "One moment…" once. The threshold is configurable.

**Model.**
- Claude Haiku 5.5 (`claude-haiku-5-5`) or Claude Sonnet 5.5 (`claude-sonnet-5-5`).
- We run the scenario suite on both and ship the faster one that passes the gate.
- Prompt caching covers the system prompt and tools.

## 7. Tickets (ask 3)

```python
class TicketSink(Protocol):
    async def create(self, request: ServiceRequest, reason: TicketReason) -> TicketRef: ...
    async def find_open(self, key: str) -> TicketRef | None: ...
    async def comment(self, ref: TicketRef, body: str) -> None: ...
    async def set_priority(self, ref: TicketRef, priority: Priority) -> None: ...
```

- **Linear setup.** Tickets go to the team "Corona 311". Each department is a label, and priority uses Linear's built-in field.
- **Ticket contents.** The description holds:
  - the call ID
  - the summary and location
  - the reason the call became a ticket
  - the callback number, taken from Twilio's caller ID (`From`) and left empty for browser calls
- **Mock.** `MockSink` records tickets in memory for the evals.
- **Outbox.** If Linear fails after one retry with a short timeout, the ticket goes to a local outbox and the caller still gets a reference number. A background task sends the outbox once Linear is back.
- **Logging.** Logs never contain raw caller numbers, only hashed IDs.
- **Gap backlog** (stretch goal):
  - one deduplicated ticket per missing-topic cluster, carrying the `gap` label and a search key
  - fields: total asks, distinct callers, first and last seen, and sample phrasings
  - each new ask adds a "+1" comment
  - priority comes from the ask rate over the last 7 days, with thresholds in config: 1 → low, ≥3 → medium, ≥10 → high

## 8. Telephony (ask 4)

**Delivered: the browser call and a Twilio trial phone line.**
- The page at `/call` streams the microphone to the Pipecat pipeline over a WebSocket. A transfer is decided and announced exactly as on a phone, and the page shows it with the department's number. Tickets are real.
- The phone line answers with the same pipeline, knowledge, tickets and routing. Phone and web share one line: one call at a time, each capped at 10 minutes.

**Phone flow.**
- **Inbound.** `POST /twilio/voice` admits Twilio by a secret `?key=` in the webhook URL, or for a number on our own account by `X-Twilio-Signature` (403 otherwise). The trial number is Twilio-owned and signed with Twilio's own token, so the trial uses the key, and every follow-up URL carries it. A busy line gets a spoken apology. Otherwise it issues a single-use ticket and returns `<Connect action="/twilio/after-stream"><Stream url="wss://…/ws/twilio"><Parameter name="ticket"/></Stream></Connect>`, so only the call Twilio announced can open the media WebSocket.
- **Media.** Pipecat's `TwilioFrameSerializer` handles 8 kHz μ-law with the same pipeline. Only the serializer differs from the browser. `auto_hang_up` is off, because the call continues after the stream ends. The caller's number becomes the ticket's callback number.
- **Transfer.** The transfer tool records the request and ends the stream. Twilio then requests `/twilio/after-stream`, which returns `<Dial action="/twilio/dial-status" answerOnBridge="true">` to `TRANSFER_TO`.
  - A `<Number url="/twilio/whisper">` whisper says, for example, "Corona city line. Public Works transfer. The caller says … Press 1 to accept." It uses `<Gather>`.
  - The app tracks acceptance itself. If nobody presses 1, or the dial fails, `/twilio/dial-status` files a `transfer_failed` ticket and reads its reference to the caller.
  - `TRANSFER_TO` comes from Secret Manager and is never committed.
- **Time limit.** At the limit the caller hears a goodbye and the call hangs up.
- **Account.** A Twilio trial gives one number in the account's own country (Israel), voice only in that country, and calls only verified numbers, at most 5, verified by SMS. It ends about 30 days after sign-up. A US demo number and open calling need an upgraded account. The caller must not be the `TRANSFER_TO` phone, so a demo needs two verified phones.

## 9. Latency

- **Target:** about 1.5 s from the caller going quiet to the agent's first audio. **Ceiling:** 2 s of silence.
- **Instrumentation.** For every turn we log the time spent in each stage:
  - turn detection
  - the final speech-to-text transcript
  - the LLM's first token
  - text-to-speech's first audio
  - the total
- These measured numbers go into the writeup.
- Barge-in uses Pipecat's interruption handling. When the caller interrupts, playback stops, the in-flight response is cancelled, and the history keeps only what was actually spoken.

## 10. Evals (ask 6)

| Layer | What it checks | When it runs |
|---|---|---|
| 1. Unit (pytest) | Hours, including Friday, 17:59 versus 18:00, closing-soon, holidays and DST. Also the parser's table-of-contents match, the chunker, gap dedupe and priority, and the outbox | Every push, blocking |
| 2. Retrieval | Hit@3 and MRR on expected `cite_id`s. Abstention precision and recall across τ, which gives the τ plot | Every push, blocking |
| 3. Scenarios | About 40 YAML conversations, as text, against the real prompt, model and tools | Pushes to main and on demand. Exact checks block |
| 4. Audio smoke | Committed caller WAV files played through the browser transport, checking the outcome and per-stage latency | On demand, by manual trigger |

- **Scenario schema:** `id`, `ask`, `clock`, `caller` (scripted lines), `expect`. `expect` can check:
  - which tool was called
  - the department
  - the outcome
  - the ticket fields
  - the citation
  - that the agent abstained
  - the emergency script
  - mentions of the next opening time
- **Gate.** Each scenario runs 3 times.
  - A trap scenario (ask 6) must pass all 3 runs.
  - Any other scenario may miss 1 of the 3, since the model's wording varies between runs.
  - The gate fails if any scenario misses more than that, or if 2 or more scenarios miss a run, which points to real drift rather than noise.
  - A scenario marked `known_issue` never blocks.
- **Simulated callers.** About 5 LLM-played callers, each with a persona and a goal, are report-only.
- **Judge.**
  - The judge is Claude Sonnet 5.5 with structured output, scored against a reference answer for each scenario.
  - The rubric: correct, grounded, dated, and short enough for speech.
  - Judge scores are tracked as a trend and never fail a run.
- **Traps:**
  - questions the code doesn't answer
  - off-topic requests
  - prompt injection
  - an emergency
  - a caller who changes their mind
  - borderline hours
- **Report.** Each run produces an HTML report as a CI artifact. It shows pass or fail per ask, the retrieval metrics, the τ plot, judge trends and latency percentiles.

## 11. Hosting and delivery

- **Google Cloud Run**, one container from the repo's Dockerfile.
  - 1 vCPU and 2 GiB. The pipeline needs at least 0.5 CPU. One call peaks at about 320 MB.
  - Memory grows about 30 MB per call while the Python heap stays flat. The growth is bounded rather than chased: after a call, if the process holds more than `RECYCLE_RSS_MB` (1400) and the line is free, the server stops itself and Cloud Run starts a fresh one.
  - At most one instance, and one call at a time.
  - One instance stays warm (`--min-instances 1`), so callers never wait on a cold start and Twilio's 15 s webhook limit is safe. Idle warm time costs about $0.65 a day, paid from the Google Cloud free-trial credit; the service is deleted after the interview.
  - Secrets in Secret Manager, each mounted at deploy: `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `DEEPGRAM_API_KEY`, `LINEAR_API_KEY`, `GAP_LOG_SALT`, `ACCESS_CODE`, plus `TWILIO_AUTH_TOKEN` (signature check for numbers on our own account), `TWILIO_WEBHOOK_KEY` (the webhook URL key the trial number relies on) and `TRANSFER_TO` (the number a phone transfer dials).
  - On shutdown, pending outbox tickets get one more try, within 6 s.
- **The public link is gated.** Each call spends API credit.
  - **Code:** `ACCESS_CODE` is held in Secret Manager. Unset, the gate is off. `GET /session` says whether a code is required.
  - **Ticket:** `POST /session` with the right code returns a one-use ticket valid for 60 s. `/ws/browser` needs it, and closes with 1008 if it is missing, used or expired.
  - **Lockout:** 10 wrong codes within 10 minutes lock the endpoint for 60 s, for everyone.
  - **Busy:** a second caller is closed with 4002 `busy`, and the page says the line is busy.
  - **Time limit:** at `MAX_CALL_SECONDS` (600) the page gets `{"type": "call-ended", "reason": "time-limit"}`, then the socket closes with 4003.
- **The service is deleted after the interview** (`gcloud run services delete`), so nothing stays public or billed.
- **Rejected:**
  - Render free. Its 0.1 CPU can't run the pipeline, as measured.
  - Vercel and Netlify. Their functions can't hold a voice WebSocket for a call.
  - Hugging Face Spaces. Docker Spaces need a paid plan.
  - Render Starter, at $7 a month, is the paid fallback.
- **CI on pull requests:** ruff, pyright (strict), pytest, then the retrieval eval.
- **CI on `main`:** the same checks, the retrieval eval, the scenario suite, then deploy. The deploy builds the image with `index.sqlite` baked in and pushes it to Artifact Registry, which keeps the newest 3 images. It authenticates with Workload Identity Federation, so no key is stored.
- **Manual-trigger workflows** (`workflow_dispatch`), both built so they can be scheduled later:
  - **content refresh:** crawl, rebuild the index, gate, redeploy
  - **audio smoke test**

## 12. Engineering standard (goes into `CLAUDE.md` at M0)

- **Tooling.** Python 3.12+ and uv. ruff for linting and formatting, with a broad rule set. pyright in strict mode, and pytest with pytest-asyncio.
- **Modern, idiomatic Python, used where it makes the code clearer:**
  - typed unions with exhaustive `match`
  - `Protocol` ports
  - frozen dataclasses for domain values
  - Pydantic at the I/O boundaries
  - `StrEnum`
  - `zoneinfo`
  - `asyncio.TaskGroup`
- **No obvious comments.** Code explains itself through naming and structure, and the reasoning goes in commit messages.
- **Small, single-purpose modules.** `domain/` stays pure.

## 13. Commits, PRs, writeup

- **Commit messages:** Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`, `docs:`). The body explains why. Every commit ends with the `Co-Authored-By: Claude` trailer.
- **Every commit passes the checks.**
- **PRs:** one short PR per milestone. The PR description serves as the design note for that milestone.
- **Milestones:**
  - M0: scaffold
  - M1: domain
  - M2: knowledge
  - M3: tickets
  - M4: the agent working in text, with evals
  - M5: voice
  - M6: deploy the web line (Cloud Run, gate, cap). The phone line followed in M6b (§8)
  - M7: writeup
- **Voice cutoff.** M5 gets a time box, set when M5 starts. If voice is still unstable when the box runs out, Vapi is pointed at the same tool backend so the live link works, and the writeup compares the two.
- **`WRITEUP.md`** sits at the repo root, is linked from the README, and fits on one page. It covers:
  - key decisions
  - a Mermaid components diagram
  - what we cut, with the real-versus-mocked table
  - what isn't working
  - what we'd build next
  - hours actually spent, with latency and eval results

## 14. Assumptions to verify during the build

| Assumption | Milestone | If it's false |
|---|---|---|
| American Legal's Download gives a parseable file, in HTML or Word format | M2 | Save the chapter pages from the browser instead |
| The Voyage free tier covers the index and the evals | M2 | Use a small local embedding model in the same index |
| The Anthropic API signup credit, about $5, covers development and evals | M4 | It's a small, deliberate API expense |
| The ElevenLabs free plan covers development calls and the demo | M5 | **False:** it ran out on 2026-10-25. Moved to Deepgram Aura-2; ElevenLabs Starter is the paid fallback |
| The pipeline fits in Render's free tier | M6 | **False:** 512 MB fits one call, but 0.1 CPU can't run it. Moved to Cloud Run |
| Cloud Run's free allowance covers the demo period | M6 | A few dollars, as a deliberate choice |
| The Twilio trial's verified-number limit covers everyone who needs to call | — | Applies: interviewers' numbers are verified by SMS in the Twilio console before the interview, at most 5 |
