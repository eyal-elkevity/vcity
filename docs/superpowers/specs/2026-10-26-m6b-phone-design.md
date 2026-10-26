# M6b: Phone Line on the Twilio Trial: Design

## Intent

The Threefold task's ask 4 is a phone line that routes callers. M6 delivered the browser call; this milestone adds a real phone number that reaches the same agent, so the interviewers can dial in and hear a live transfer ring Eyal's phone. Spec §8 holds the original design; this document adapts it to a free Twilio trial and the deployed Cloud Run service.

## Goals

- The trial's Israeli Twilio number answers with the same agent, knowledge, tickets and routing as `/call`.
- A transfer dials `TRANSFER_TO`. The person who answers hears a short whisper and presses 1 to accept. If nobody accepts, or the dial fails, the caller hears a `transfer_failed` ticket reference instead.
- Phone and web share one line: one call at a time across both, each capped at 10 minutes.
- Nothing on the phone path costs money beyond the Twilio trial's free minutes and the existing Cloud Run credit.

## Non-goals

- Outbound calls, SMS, call recording, voicemail.
- A US number, or calls from unverified numbers. Both need a paid Twilio account.
- Pretend office hours on the phone. The phone uses the real clock.
- Reconnecting the caller to the agent after a failed transfer.

## Anti-goals

- A webhook anyone can call to run the pipeline. Every Twilio webhook checks `X-Twilio-Signature`.
- The auth token, `TRANSFER_TO` or any caller's number in the repo, the image or the logs.
- A transfer that lands in voicemail and counts as answered.

## Constraints (checked on 2026-10-26)

- **The trial account** (signed up 2026-10-25, ends about 2026-11-24): one Twilio-provided number, Israeli (+972). Voice works in the sign-up country only. Only verified numbers can call it or be called, up to 5, verified by SMS. 73 free voice minutes remain. Twilio may play a trial notice before our TwiML runs.
- **Found in the live test (2026-10-26):** the Try out Voice number blocks `<Stream>` and `<Dial><Number>` (Twilio replaces them with a trial message), so the line cannot go live without an upgraded account and a number of its own.
- **Inbound configuration** is the console's *Try out Voice → Receive a call → Custom* panel: a webhook URL with POST. Eyal sets it once to `https://<service>/twilio/voice`.
- **Media Streams.** `<Connect><Stream>` needs `wss://`, and its `url` takes no query string, so values travel as `<Parameter>` and arrive in the stream's `start` message. Twilio runs the TwiML after `<Connect>` (or requests its `action`) only once the server closes the WebSocket.
- **Webhook timeout.** Twilio waits about 15 s for a webhook answer. One Cloud Run instance stays warm (PR #16), so cold starts don't apply.
- **Pipecat 1.14.0** ships `TwilioFrameSerializer` (8 kHz μ-law ⇄ PCM, resampling both ways). Its `auto_hang_up` ends the call through Twilio's REST API on `EndFrame`; we turn it off, because the call must continue to the transfer after the stream ends.
- **Signature.** HMAC-SHA1 over the full URL plus the POST parameters sorted by name and concatenated, keyed with the auth token, Base64. Twilio drops the port for HTTPS voice callbacks. Behind Cloud Run the app sees `http://`, so uvicorn must trust `X-Forwarded-Proto`.
- **Whisper.** `<Number url>` TwiML may only use `<Say>`, `<Play>`, `<Gather>` and `<Hangup>`. Twilio's docs don't define the `DialCallStatus` after a declined whisper, so the app records acceptance itself.

## Decisions taken

### Call flow
1. **`POST /twilio/voice`.** The server checks the signature (403 if it fails).
   - If the line is busy, it answers `<Say>` "The city line is busy. Please try again in a minute." then `<Hangup/>`.
   - Otherwise it issues a single-use ticket from the existing `TicketBook` and answers `<Connect action="/twilio/after-stream"><Stream url="wss://<host>/ws/twilio"><Parameter name="ticket" value="…"/></Stream></Connect>`.
2. **`/ws/twilio`.** The server accepts the socket and reads Twilio's `connected` and `start` messages. From `start` it takes `streamSid`, `callSid` and the `ticket` custom parameter.
   - A bad or used ticket closes the socket before any pipeline work, as on the web.
   - A busy line closes it too; `/twilio/after-stream` then says the busy line.
   - Otherwise the same pipeline runs with `TwilioFrameSerializer(stream_sid, params=InputParams(auto_hang_up=False))`, at 8 kHz in and out. The caller's number (`From`, from step 1) becomes `CallContext.caller_id`, so tickets carry a callback number. The call id is `TEL-` plus the last 8 characters of the `CallSid`.
3. **The stream ends** when the caller hangs up, after a transfer is announced (the existing `EndWorkerFrame` path), or at the time limit. Twilio then posts `/twilio/after-stream`, and the server answers from what it recorded for that `CallSid`:
   - **Transfer:** `<Dial action="/twilio/dial-status" answerOnBridge="true" timeout="20"><Number url="/twilio/whisper">TRANSFER_TO</Number></Dial>`.
   - **Time limit:** `<Say>` "Calls on this line are limited to ten minutes. Goodbye." then `<Hangup/>`.
   - **Busy** (the socket was refused): the busy `<Say>` then `<Hangup/>`.
   - **Anything else:** `<Hangup/>`.
4. **`POST /twilio/whisper`** is played to the person answering, before the bridge. It answers `<Gather numDigits="1" action="/twilio/whisper-answer" timeout="8"><Say>` "Corona city line. <Department> transfer. The caller says: <summary>. Press 1 to accept." `</Say></Gather><Hangup/>`.
5. **`POST /twilio/whisper-answer`.** With `Digits=1` it marks the parent call accepted and answers an empty `<Response/>`, which bridges the call. Any other digit gets `<Hangup/>`.
6. **`POST /twilio/dial-status`** fires when the dial ends.
   - If the call was accepted, the answer is `<Hangup/>`; the conversation already happened.
   - If not, the server files a `transfer_failed` ticket (`next_open=None`) through the existing ticket sink, and answers `<Say>` "Sorry, nobody could take your call right now. I've filed a ticket, reference <digits>. Someone will call you back." `<Hangup/>`.
   - If filing fails, it says the matching apology without a reference.

### State
- **`PhoneCalls`**, an in-memory registry keyed by `CallSid`, holds `caller_id`, the outcome (`transfer` with its `TransferRequest`, `time_limit`, `busy`) and `accepted`.
- Entries are removed at `dial-status`, or at `after-stream` when no dial follows. They expire after 30 minutes as a backstop. A single instance makes memory enough, as with the gate.

### Code shape
- `run_browser_call` becomes a shared `run_call` that takes a transport factory (browser protobuf or Twilio), the sample rate and the hours mode. The browser keeps its behaviour, including the RTVI messages; the Twilio serializer drops RTVI frames.
- `ScreenTransfer` becomes `CallTransfer`; `take()` keeps its semantics for ending the turn.
- The phone surface records its outcome into `PhoneCalls` the moment it is decided: the transfer before the worker ends, the time limit before the socket closes. Twilio requests `/twilio/after-stream` as soon as the socket closes, often before Pipecat finishes tearing down, so the outcome cannot wait for `run_call` to return.
- On the phone only, the agent greets the caller in its own voice when the stream connects: "Thanks for calling the City of Corona. How can I help you today?" Without it, a caller hears silence after the trial notice.
- The memory recycler stops the process only when the line is free and no phone call is still between webhooks, because the dial, whisper and dial-status steps need the in-memory `PhoneCalls`.
- Twilio routes live in `src/vcity/telephony/`: `signature.py` (validation), `twiml.py` (responses built with `xml.etree.ElementTree`, so caller text is escaped), `calls.py` (`PhoneCalls`), `routes.py` (the FastAPI routes), `stream.py` (start-message parsing).
- `TwiML <Say>` uses Twilio's default voice. Only these fixed lines and the whisper use it; the agent itself still speaks as Thalia.

### Settings and secrets
- `TelephonySettings`: `twilio_auth_token: SecretStr | None`. The routes are mounted only when it's set; locally without it they return 404.
- `TRANSFER_TO` keeps its existing `AppSettings` field. On Cloud Run both come from Secret Manager, created on 2026-10-26.
- `deploy.yml` adds `TWILIO_AUTH_TOKEN` and `TRANSFER_TO` to `--set-secrets`. `tools/gcp_setup.sh` and `tools/gcp_secrets.sh` add both names.
- `__main__.py` runs uvicorn with `proxy_headers=True` and `forwarded_allow_ips="*"`. Cloud Run's front end is the only path in, so its `X-Forwarded-Proto` is trusted, and the URL the app rebuilds matches the one Twilio signed.

### Logging
Only `CallSid` suffixes, outcomes and counts. Never `From`, `To`, `TRANSFER_TO`, digits, the summary or the whisper text.

### Signature validation
Implemented in about 20 lines with `hmac`, `hashlib` and `hmac.compare_digest`, not the `twilio` SDK, which would add a large dependency for one function. It's pinned by Twilio's documented test vector: URL `https://example.com/myapp.php?foo=1&bar=2`, the five parameters, token `12345`, signature `L/OH5YylLD5NRKLltdqwSvS0BnU=`.

## Testing

- **Unit:**
  - the signature (the documented vector, a forged signature, a missing header, a tampered parameter)
  - TwiML builders (escaping, exact verbs)
  - `PhoneCalls` (outcomes, acceptance, expiry)
  - start-message parsing (good, missing ticket, wrong event order)
- **App, with fakes and no pipeline:**
  - voice webhook: busy and free line, bad signature
  - the stream socket: bad ticket, busy line, a good ticket reaching the call with the right `caller_id`
  - each after-stream outcome
  - whisper accept and decline
  - dial-status: accepted, not accepted with the ticket filed and its reference spoken, filing failed
- **Live, once deployed, about 5 minutes of trial time:**
  - a call from Eyal's phone asking a code question
  - a transfer to Eyal's phone, accepted with 1
  - a transfer left unanswered, confirming the ticket and its reference

## Rollout

1. Merge. CI deploys with the two new secrets.
2. Eyal sets the Custom webhook URL in the Twilio console to `https://vcity-dbykunzueq-uw.a.run.app/twilio/voice`, method POST.
3. Eyal places the live calls above. I check Cloud Run logs for outcomes only.
4. Before the interview, Eyal verifies the interviewers' numbers by SMS in the Twilio console. The caller must not be `TRANSFER_TO`'s phone: a transfer to the phone that is placing the call finds it busy and falls back to a ticket. The live test and the demo therefore need two verified phones, a caller plus Eyal's.

## Left to the builder

- Exact `<Say>` wording, within the lines above.
- How `run_call` receives the transport, a factory function or a small dataclass, as long as the browser path is unchanged.
- Test seams for Twilio messages: plain JSON fixtures.
