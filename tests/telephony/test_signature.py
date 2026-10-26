from vcity.telephony.signature import signature_matches, twilio_signature

VECTOR_URL = "https://example.com/myapp.php?foo=1&bar=2"
VECTOR_PARAMS = {
    "Digits": "1234",
    "To": "+18005551212",
    "From": "+14158675310",
    "Caller": "+14158675310",
    "CallSid": "CA1234567890ABCDE",
}
VECTOR_SIGNATURE = "L/OH5YylLD5NRKLltdqwSvS0BnU="


def test_matches_twilios_documented_vector() -> None:
    assert twilio_signature(VECTOR_URL, VECTOR_PARAMS, "12345") == VECTOR_SIGNATURE


def test_accepts_the_right_signature() -> None:
    assert signature_matches(VECTOR_URL, VECTOR_PARAMS, "12345", VECTOR_SIGNATURE)


def test_rejects_a_missing_signature() -> None:
    assert not signature_matches(VECTOR_URL, VECTOR_PARAMS, "12345", None)


def test_rejects_a_tampered_parameter() -> None:
    assert not signature_matches(
        VECTOR_URL, {**VECTOR_PARAMS, "Digits": "1"}, "12345", VECTOR_SIGNATURE
    )


def test_rejects_another_token() -> None:
    assert not signature_matches(VECTOR_URL, VECTOR_PARAMS, "54321", VECTOR_SIGNATURE)
