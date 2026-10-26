import os

import uvicorn

from vcity.app import create_app
from vcity.settings import AppSettings, TelephonySettings, VoiceSettings, WebSettings
from vcity.voice.logs import configure_logging

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
GRACEFUL_SHUTDOWN_SECONDS = 2


def main() -> None:
    configure_logging()
    app = create_app(
        AppSettings.from_env(),
        VoiceSettings.from_env(),
        WebSettings.from_env(),
        telephony=TelephonySettings.from_env(),
    )
    uvicorn.run(
        app,
        host=os.environ.get("HOST", DEFAULT_HOST),
        port=int(os.environ.get("PORT", DEFAULT_PORT)),
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        # Cloud Run terminates TLS; Twilio's signed https scheme is only in X-Forwarded-Proto.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
