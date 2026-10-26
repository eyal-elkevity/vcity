# M6: Deploy the Web Call Line: Design

## Intent

The Threefold task asks for "a live link where they can try the agent themselves". M6 puts the browser call (`/call`) on a public HTTPS URL that the interviewers can open from anywhere. Two things must hold:
- **Nobody but them can spend Eyal's API credit.** That's Anthropic, Deepgram and Voyage, plus Linear tickets.
- **The small server never takes more than it can carry.**

After the interview the link is shut down.

## Goals

- A public `https://…run.app/call` URL, deployed automatically from `main` once CI passes.
- An access code the interviewers type once. Without it, no call can start and no paid API is touched.
- One call at a time, each capped at 10 minutes.
- Memory stays bounded across many calls (see Constraints).
- The infrastructure is reproducible from the repo: a setup script plus a workflow, with no hand-clicked state beyond creating the project and linking billing.

## Non-goals

- User accounts, sign-in, or per-person codes.
- More than one concurrent call, autoscaling, or multiple regions.
- A custom domain.
- The Twilio phone line, which is spec §8's documented next step.

## Anti-goals

These would make M6 a failure even if it technically works:
- A leaked page URL that lets strangers run calls. The code must be checked by the server, not just the page.
- Secrets or the access code in the public repo, in the image, or in logs.
- A long-lived Google key stored in GitHub.
- A deploy that ships code CI hasn't passed.
- Anything exposed from Eyal's laptop. No tunnels, ever (Genoox security review, 2026-10-25).

## Constraints (measured on 2026-10-26)

- **CPU.** The pipeline needs at least 0.5 CPU (spec §0). Cloud Run gives 1 vCPU.
- **Memory grows about 30 MB per call and isn't returned.**
  - Measured in the container on the local Mac with 1 vCPU and 1 GiB: 158 MB idle, 903 MB after 28 calls, with no plateau.
  - **Ruled out:**
    - the Python heap: tracemalloc is flat, 118.8 → 118.9 MB over 8 calls, and object counts are flat
    - the per-call ONNX models: about 4 MB a call
    - audio resamplers
    - TLS contexts
    - allocators: glibc `MALLOC_ARENA_MAX=2` and jemalloc both still grow
  - The cause is native memory held somewhere in Pipecat or its dependencies. Chasing it further is out of the time box. M6 **bounds** it instead of fixing it.
- **The image** is about 2 GB, starts in about 7 s, and includes the warm-up model call.
- **The spike deploy works.** A private, IAM-only revision in `us-west1` with 1 vCPU and 2 GiB passed the audio smoke over `wss://`, 4/4 at 1.25–1.59 s to first audio. The spike was then deleted.
- **The project** is `vcity-512721`, on Eyal's personal Google account, with billing linked and the needed APIs enabled. Region `us-west1`, Oregon, is near Corona's time zone.
- **Cloud Run CPU billing.** With the default request-based billing, CPU is throttled between requests. The open voice WebSocket counts as a request, so calls are unaffected. The ticket outbox retry and gap backlog loops only make progress while a request is in flight. That's acceptable at demo scale.

## Approaches considered (deploy pipeline)

| | Approach | Trade-off |
|---|---|---|
| **Chosen** | **GitHub Actions with Workload Identity Federation.** After CI's checks pass on `main`, a job builds the image, pushes it to Artifact Registry and runs `gcloud run deploy`. GitHub authenticates to Google with a short-lived OIDC token, so no key is stored anywhere. | About 10 minutes of one-time setup, scripted. |
| Rejected | Cloud Build GitHub trigger | It needs a console-clicked GitHub App connection, and it doesn't wait for our CI without extra wiring. |
| Rejected | GitHub Actions with a service-account JSON key | A long-lived cloud key sitting in a repo secret, which is the anti-goal above. |

Eyal picked the first approach. Deploys are automatic on every merge to `main`, since no development happens in the last day before the interview.

## Decisions taken

### Image (`Dockerfile`, `.dockerignore`)
- The base is `python:3.12-slim`, with `uv` copied from `ghcr.io/astral-sh/uv:0.10.9`, and dependencies installed with `uv sync --locked --no-dev`. Dependencies go in their own layer before the source.
- The workflow downloads `build/index.sqlite` from the `knowledge-index` release (as CI already does) and copies it into the image, so the image is reproducible from the workflow run. The prototype's `ADD <url>` is dropped.
- The environment sets `HOST=0.0.0.0`. Cloud Run sets `PORT`.
- `.dockerignore` excludes `.venv`, `.env*`, `.git`, `.idea`, `.superpowers`, `docs`, `reports`, and `build/` except `build/index.sqlite`.

### Access code (the gate)
- **Where it lives.** `ACCESS_CODE` is a Secret Manager secret, exposed to the service as an environment variable. It's never in the repo or the image.
- **Exchange.**
  - On load the page calls `GET /session`, which answers `{"code_required": true|false}`. When it's false the page skips the field.
  - The page shows a code field. On submit it calls `POST /session` with `{"code": …}`.
  - A wrong code gets `401`. If a call is already running, a right code gets `409` and the page shows the busy message, so no socket is opened.
  - The server compares the code to `ACCESS_CODE` in constant time.
  - If it matches, the server returns a **ticket**: a signed, single-use token valid for 60 s, keyed with a random secret created at process start.
  - The page opens `/ws/browser?ticket=…`.
  - Why: the code itself never appears in a URL, where Cloud Run's request logs would record it. Only short-lived tickets do.
- **The WebSocket checks the ticket before anything starts.** An invalid, used or expired ticket closes with WebSocket code 1008 before any pipeline, STT, TTS or LLM work.
- **Slowing guessing.**
  - After 10 wrong codes within 10 minutes, from anyone, `POST /session` answers `429` for 60 s.
  - The lockout is global, not per address. A per-address limit would trust `X-Forwarded-For`, which a client can forge, and only a handful of people will ever have the link.
  - The counter lives in memory, which works because there's a single instance.
- **Convenience.** The page keeps the code in `sessionStorage`, so it's typed once per tab, and re-shows the field on a 401.
- **Without `ACCESS_CODE` set** (local development), the gate is off: `GET /session` reports `code_required: false`, `POST /session` issues a ticket for any code, and the WebSocket still requires a ticket. Tests cover both modes.

### Capacity
- **How the page learns why a call ended.** Pipecat's browser client doesn't expose WebSocket close codes, so the page never depends on them. Busy is caught at `POST /session` (409). The time limit is announced with an RTVI server message before the socket closes.
- **One call at a time.**
  - The app holds a single "line busy" slot, taken when the WebSocket opens with a valid ticket.
  - `POST /session` answers `409` while the slot is taken, and the page shows "The line is busy. Try again in a minute."
  - A WebSocket that still races in while the slot is taken is closed with app code 4002 and reason `busy`, before any pipeline work.
  - Cloud Run concurrency is set above 1, so `/call`, `/session` and `/health` still answer during a call.
- **10-minute call cap.**
  - Each call runs under a 600 s deadline, configurable as `MAX_CALL_SECONDS`.
  - When it fires, the server sends the RTVI server message `{"type": "call-ended", "reason": "time-limit"}`, cancels the pipeline, and closes the socket with app code 4003, reason `time limit`.
  - On that message the page shows "Calls are limited to 10 minutes."
  - Cloud Run's request timeout is 900 s, so Cloud Run never cuts a call before the app does.
- **Instances.** Cloud Run runs at most 1 instance (`--max-instances 1`), with min instances 0.

### Memory bound
- Cloud Run memory is **2 GiB**, about 60 calls of headroom at the measured growth.
- **Recycling.**
  - After each call ends, if the process's RSS is above `RECYCLE_RSS_MB` (default 1400) and no call is active, the app sends itself SIGTERM.
  - Uvicorn shuts down gracefully, the container exits, and Cloud Run starts a fresh instance on the next request.
  - The line logs this, but only the RSS figure, never transcripts.
- Cloud Run also recycles idle instances on its own, so in practice this rarely triggers.

### State lost on restart
- Cloud Run's filesystem is in memory, so `build/outbox.sqlite` and `build/gaps.jsonl` vanish on recycle, scale-to-zero and deploy.
- **Pending Linear tickets are flushed on shutdown.** On SIGTERM, before the process exits, the outbox makes one delivery attempt for every pending ticket, bounded by 8 s so it fits Cloud Run's 10 s shutdown window. Tickets that still fail are logged by count only.
- **The gap log may be lost.** Gap tickets already sent to Linear survive. The local backlog only batches what hasn't been sent yet, and losing it on restart is acceptable.

### Secrets and identity
- **Secrets.** Secret Manager holds `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `DEEPGRAM_API_KEY`, `LINEAR_API_KEY`, `GAP_LOG_SALT` and `ACCESS_CODE`. `TRANSFER_TO` isn't deployed, because the browser shows transfers rather than dialling.
- **Runtime service account `vcity-run`.** Its only role is `secretmanager.secretAccessor` on those secrets.
- **Deployer service account `vcity-deploy`.** GitHub Actions impersonates it through WIF. Its roles are `run.admin` on the project, `artifactregistry.writer` on the `vcity` repo, and `iam.serviceAccountUser` on `vcity-run`.
- **WIF provider.** It's restricted to the GitHub repo `eyal-elkevity/vcity` and `refs/heads/main`. A reusable workflow's token carries the caller's `ref`, so the condition holds for both callers.

### Infrastructure as code
- **`tools/gcp_setup.sh`** is idempotent. It creates the Artifact Registry repo, both service accounts, the WIF pool and provider, the bindings, and empty secrets. Eyal runs it once with `gcloud`, then fills secret values with `gcloud secrets versions add`, piped from his `.env` so values aren't echoed.
- **CI order on `main`.** Deploys wait for every automated eval layer, as spec §11 promises.
  - `ci.yml` on `push` to `main`: `checks` → `retrieval-eval` → `scenarios` → `deploy`. Pull requests stop after `retrieval-eval`.
  - `scenarios.yml` becomes a reusable workflow (`workflow_call`) and keeps `workflow_dispatch` for on-demand runs. Its own `push` trigger is removed, so the suite runs once per merge.
  - A failed or flaky scenario run blocks the deploy until it's rerun. Each merge costs about 10 minutes and $0.04 of Anthropic credit.
- **`.github/workflows/deploy.yml`** is a reusable workflow (`workflow_call`). `ci.yml` calls it, and so does `content-refresh.yml` after its rebuild and retrieval gate, so a content refresh also redeploys.
  - Callers grant `id-token: write` and pass `secrets: inherit`.
  - Steps: `google-github-actions/auth` (WIF), build with docker (`linux/amd64` runners), push to `us-west1-docker.pkg.dev/vcity-512721/vcity/vcity:<sha>`, then `gcloud run deploy vcity`.
  - The deploy flags pin CPU, memory, instances, concurrency, timeout, secrets and service account, so the command is the full source of truth.
- **Registry cleanup.** The setup script applies an Artifact Registry cleanup policy that keeps the newest 3 images and deletes the rest: about 6 GB, roughly $0.55 a month until shutdown.
- **The public service** is deployed `--allow-unauthenticated`, because the access code is the gate. The project ID, region and WIF provider names are workflow `env:` values. They're not secrets.

### Docs
- **README:** how to run the setup script, set secrets, find the URL, rotate the code, and shut the service down (`gcloud run services delete vcity`).
- **Spec §11:** updated to match.
- **Memory note:** the native memory growth is recorded for the writeup as a known issue with its bound.

## Left to the builder

- Exact page copy and layout of the code field, within the Circle City design.
- Token format for the ticket: HMAC-SHA256 over a random nonce and expiry is enough.
- How the call deadline is implemented: `asyncio.timeout` around the runner, or a watchdog task.
- Test seams for the gate, capacity and recycle logic, which should run without a pipeline.
