from vcity.voice.call import measured_from_caller


def test_a_reply_measured_from_the_caller_counts() -> None:
    assert measured_from_caller({"stt": 0.21, "llm": 0.9, "tts": 0.35})


def test_a_reply_anchored_on_the_greeting_span_is_skipped() -> None:
    assert not measured_from_caller({"first request": 37.1, "llm": 0.9, "tts": 0.35})
