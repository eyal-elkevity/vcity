import xml.etree.ElementTree as ET

from vcity.telephony.twiml import (
    bridge,
    connect_stream,
    dial,
    hang_up,
    say_and_hang_up,
    whisper,
)

DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'


def _parse(document: str) -> ET.Element:
    return ET.fromstring(document.removeprefix(DECLARATION))  # noqa: S314


def test_connect_stream_passes_the_ticket_as_a_parameter() -> None:
    root = _parse(connect_stream("wss://h/ws/twilio", "tk", "/twilio/after-stream"))
    connect = root.find("Connect")
    assert connect is not None
    assert connect.get("action") == "/twilio/after-stream"
    stream = connect.find("Stream")
    assert stream is not None
    assert stream.get("url") == "wss://h/ws/twilio"
    parameter = stream.find("Parameter")
    assert parameter is not None
    assert parameter.get("name") == "ticket"
    assert parameter.get("value") == "tk"


def test_say_and_hang_up_orders_say_before_hangup() -> None:
    root = _parse(say_and_hang_up("Goodbye"))
    assert [child.tag for child in root] == ["Say", "Hangup"]
    assert root.findtext("Say") == "Goodbye"


def test_hang_up_is_a_single_hangup() -> None:
    root = _parse(hang_up())
    assert [child.tag for child in root] == ["Hangup"]


def test_dial_answers_on_bridge_and_whispers() -> None:
    root = _parse(dial("+15555550100", action="/d", whisper_url="/w", timeout=20))
    node = root.find("Dial")
    assert node is not None
    assert node.get("answerOnBridge") == "true"
    assert node.get("timeout") == "20"
    assert node.get("action") == "/d"
    assert root.findtext("Dial/Number") == "+15555550100"
    number = root.find("Dial/Number")
    assert number is not None
    assert number.get("url") == "/w"


def test_whisper_escapes_caller_text() -> None:
    text = 'Pothole <big> & "deep" — ñ'
    root = _parse(whisper(text, action="/a", timeout=8))
    gather = root.find("Gather")
    assert gather is not None
    assert gather.get("numDigits") == "1"
    assert gather.get("action") == "/a"
    assert gather.get("timeout") == "8"
    assert root.findtext("Gather/Say") == text
    assert root.find("Hangup") is not None


def test_bridge_is_an_empty_response() -> None:
    root = _parse(bridge())
    assert root.tag == "Response"
    assert len(root) == 0


def test_documents_declare_utf8() -> None:
    documents = [
        connect_stream("wss://h/ws/twilio", "tk", "/a"),
        say_and_hang_up("x"),
        hang_up(),
        dial("+15555550100", action="/d", whisper_url="/w", timeout=20),
        whisper("x", action="/a", timeout=8),
        bridge(),
    ]
    for document in documents:
        assert document.startswith(DECLARATION)
        assert document.removeprefix(DECLARATION).startswith("<Response")
