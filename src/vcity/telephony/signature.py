import base64
import hashlib
import hmac
from collections.abc import Mapping


def twilio_signature(url: str, params: Mapping[str, str], token: str) -> str:
    payload = url + "".join(name + params[name] for name in sorted(params))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def signature_matches(
    url: str, params: Mapping[str, str], token: str, signature: str | None
) -> bool:
    if signature is None:
        return False
    expected = twilio_signature(url, params, token)
    return hmac.compare_digest(expected.encode(), signature.encode())
