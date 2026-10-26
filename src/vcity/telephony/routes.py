import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

from fastapi import APIRouter, Request, Response, WebSocket
from pydantic import SecretStr

from vcity.agent.routing import TransferRequest
from vcity.agent.tools import CallContext
from vcity.domain.tickets import TicketRef
from vcity.gate.line import CallLine
from vcity.gate.tickets import TicketBook
from vcity.telephony import twiml
from vcity.telephony.calls import Outcome, PhoneCall, PhoneCalls, phone_call_id
from vcity.telephony.signature import signature_matches
from vcity.telephony.stream import StreamStart, read_start
from vcity.voice.call import CallOutcome, OutcomeRecorder, TimeLimit, Transferred

_log = logging.getLogger(__name__)

type PhoneRunner = Callable[[WebSocket, StreamStart, str | None, OutcomeRecorder], Awaitable[None]]
type TransferTicketer = Callable[[TransferRequest, CallContext], Awaitable[TicketRef | None]]

BUSY_LINE = "The city line is busy. Please try again in a minute."
TIME_LIMIT_LINE = "Calls on this line are limited to ten minutes. Goodbye."
WHISPER = "Corona city line. {department} transfer. The caller says: {summary}. Press 1 to accept."
FAILED_WITH_TICKET = (
    "Sorry, nobody could take your call right now. "
    "I've filed a ticket, reference {digits}. Someone will call you back."
)
FAILED_NO_TICKET = (
    "Sorry, nobody could take your call right now, and I couldn't file a ticket. "
    "Please call again later."
)
DIAL_TIMEOUT_SECONDS = 20
WHISPER_TIMEOUT_SECONDS = 8
POLICY_VIOLATION = 1008
BUSY_CLOSE = 4002
ENDED = "ended"
XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True, slots=True)
class _TwilioHandlers:
    secret: str
    transfer_to: str
    line: CallLine
    tickets: TicketBook
    calls: PhoneCalls
    run: PhoneRunner
    file_ticket: TransferTicketer
    recycle_when_unused: Callable[[], None]

    async def voice(self, request: Request) -> Response:
        params = await self._signed_form(request)
        if params is None:
            return _forbidden()
        if self.line.busy:
            return _twiml(twiml.say_and_hang_up(BUSY_LINE))
        self.calls.open(params.get("CallSid", ""), params.get("From") or None)
        stream_url = f"wss://{request.url.hostname}/ws/twilio"
        return _twiml(
            twiml.connect_stream(stream_url, self.tickets.issue(), "/twilio/after-stream")
        )

    async def stream(self, websocket: WebSocket) -> None:
        await websocket.accept()
        start = await read_start(websocket)
        if start is None or not self.tickets.redeem(start.ticket):
            await _close(websocket, POLICY_VIOLATION)
            return
        if not self.line.take():
            self.calls.finish(start.call_sid, Outcome.BUSY)
            _log.info("%s outcome %s", phone_call_id(start.call_sid), Outcome.BUSY)
            await _close(websocket, BUSY_CLOSE)
            return
        try:
            caller = self.calls.get(start.call_sid)
            await self.run(
                websocket,
                start,
                caller.caller_id if caller else None,
                partial(_record, self.calls, start.call_sid),
            )
            await _close(websocket, None)
        finally:
            self.line.release()
            _log.info("%s outcome %s", phone_call_id(start.call_sid), self._outcome(start.call_sid))
            self.recycle_when_unused()

    def _outcome(self, call_sid: str) -> str:
        call = self.calls.get(call_sid)
        return call.outcome if call is not None and call.outcome is not None else ENDED

    async def after_stream(self, request: Request) -> Response:
        params = await self._signed_form(request)
        if params is None:
            return _forbidden()
        call_sid = params.get("CallSid", "")
        call = self.calls.get(call_sid)
        if call is not None and call.outcome is Outcome.TRANSFER:
            return _twiml(
                twiml.dial(
                    self.transfer_to,
                    action="/twilio/dial-status",
                    whisper_url="/twilio/whisper",
                    timeout=DIAL_TIMEOUT_SECONDS,
                )
            )
        self.calls.close(call_sid)
        self.recycle_when_unused()
        return _twiml(_farewell(call.outcome if call else None))

    async def whisper(self, request: Request) -> Response:
        params = await self._signed_form(request)
        if params is None:
            return _forbidden()
        parent = self.calls.get(params.get("ParentCallSid", ""))
        if parent is None or parent.transfer is None:
            return _twiml(twiml.hang_up())
        prompt = WHISPER.format(
            department=parent.transfer.department.value.replace("_", " "),
            summary=XML_ILLEGAL.sub("", parent.transfer.summary),
        )
        return _twiml(
            twiml.whisper(prompt, action="/twilio/whisper-answer", timeout=WHISPER_TIMEOUT_SECONDS)
        )

    async def whisper_answer(self, request: Request) -> Response:
        params = await self._signed_form(request)
        if params is None:
            return _forbidden()
        parent_sid = params.get("ParentCallSid", "")
        accepted = params.get("Digits") == "1"
        _log.info("%s whisper accepted %s", phone_call_id(parent_sid), accepted)
        if not accepted:
            return _twiml(twiml.hang_up())
        self.calls.accept(parent_sid)
        return _twiml(twiml.bridge())

    async def dial_status(self, request: Request) -> Response:
        params = await self._signed_form(request)
        if params is None:
            return _forbidden()
        call_sid = params.get("CallSid", "")
        document = await self._after_dial(call_sid, self.calls.close(call_sid))
        self.recycle_when_unused()
        return _twiml(document)

    async def _after_dial(self, call_sid: str, call: PhoneCall | None) -> str:
        if call is None or call.transfer is None:
            return twiml.hang_up()
        if call.accepted:
            _log.info("%s dial accepted", phone_call_id(call_sid))
            return twiml.hang_up()
        return await _apologise(call, call.transfer, call_sid, self.file_ticket)

    async def _signed_form(self, request: Request) -> dict[str, str] | None:
        form = await request.form()
        params = {key: value for key, value in form.items() if isinstance(value, str)}
        signature = request.headers.get("X-Twilio-Signature")
        if not signature_matches(str(request.url), params, self.secret, signature):
            return None
        return params


def telephony_router(  # noqa: PLR0913
    *,
    token: SecretStr,
    transfer_to: str,
    line: CallLine,
    tickets: TicketBook,
    calls: PhoneCalls,
    run: PhoneRunner,
    file_ticket: TransferTicketer,
    recycle_when_unused: Callable[[], None],
) -> APIRouter:
    handlers = _TwilioHandlers(
        token.get_secret_value(),
        transfer_to,
        line,
        tickets,
        calls,
        run,
        file_ticket,
        recycle_when_unused,
    )
    router = APIRouter()
    router.add_api_route("/twilio/voice", handlers.voice, methods=["POST"])
    router.add_api_websocket_route("/ws/twilio", handlers.stream)
    router.add_api_route("/twilio/after-stream", handlers.after_stream, methods=["POST"])
    router.add_api_route("/twilio/whisper", handlers.whisper, methods=["POST"])
    router.add_api_route("/twilio/whisper-answer", handlers.whisper_answer, methods=["POST"])
    router.add_api_route("/twilio/dial-status", handlers.dial_status, methods=["POST"])
    return router


def _farewell(outcome: Outcome | None) -> str:
    match outcome:
        case Outcome.TIME_LIMIT:
            return twiml.say_and_hang_up(TIME_LIMIT_LINE)
        case Outcome.BUSY:
            return twiml.say_and_hang_up(BUSY_LINE)
        case _:
            return twiml.hang_up()


async def _apologise(
    call: PhoneCall, transfer: TransferRequest, call_sid: str, file_ticket: TransferTicketer
) -> str:
    call_id = phone_call_id(call_sid)
    ref = await file_ticket(transfer, CallContext(call_id, call.caller_id))
    _log.info("%s dial unanswered, ticket filed %s", call_id, ref is not None)
    if ref is None:
        return twiml.say_and_hang_up(FAILED_NO_TICKET)
    return twiml.say_and_hang_up(FAILED_WITH_TICKET.format(digits=" ".join(ref.reference)))


def _record(calls: PhoneCalls, call_sid: str, outcome: CallOutcome) -> None:
    match outcome:
        case Transferred(request=request):
            calls.finish(call_sid, Outcome.TRANSFER, request)
        case TimeLimit():
            calls.finish(call_sid, Outcome.TIME_LIMIT)


async def _close(websocket: WebSocket, code: int | None) -> None:
    with contextlib.suppress(RuntimeError):
        if code is None:
            await websocket.close()
        else:
            await websocket.close(code=code)


def _twiml(document: str) -> Response:
    return Response(content=document, media_type="application/xml")


def _forbidden() -> Response:
    return Response(status_code=403)
