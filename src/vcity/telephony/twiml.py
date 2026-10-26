import xml.etree.ElementTree as ET

DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'


def connect_stream(stream_url: str, ticket: str, action: str) -> str:
    response = ET.Element("Response")
    connect = ET.SubElement(response, "Connect", {"action": action})
    stream = ET.SubElement(connect, "Stream", {"url": stream_url})
    ET.SubElement(stream, "Parameter", {"name": "ticket", "value": ticket})
    return _document(response)


def say_and_hang_up(text: str) -> str:
    response = ET.Element("Response")
    ET.SubElement(response, "Say").text = text
    ET.SubElement(response, "Hangup")
    return _document(response)


def hang_up() -> str:
    response = ET.Element("Response")
    ET.SubElement(response, "Hangup")
    return _document(response)


def dial(number: str, *, action: str, whisper_url: str, timeout: int) -> str:
    response = ET.Element("Response")
    dial_node = ET.SubElement(
        response,
        "Dial",
        {"action": action, "answerOnBridge": "true", "timeout": str(timeout)},
    )
    ET.SubElement(dial_node, "Number", {"url": whisper_url}).text = number
    return _document(response)


def whisper(prompt: str, *, action: str, timeout: int) -> str:
    response = ET.Element("Response")
    gather = ET.SubElement(
        response,
        "Gather",
        {"numDigits": "1", "action": action, "timeout": str(timeout)},
    )
    ET.SubElement(gather, "Say").text = prompt
    ET.SubElement(response, "Hangup")
    return _document(response)


def bridge() -> str:
    return _document(ET.Element("Response"))


def _document(response: ET.Element) -> str:
    return DECLARATION + ET.tostring(response, encoding="unicode")
