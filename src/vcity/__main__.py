import os

import uvicorn

from vcity.app import create_app
from vcity.settings import AppSettings, VoiceSettings
from vcity.voice.logs import configure_logging

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def main() -> None:
    configure_logging()
    app = create_app(AppSettings.from_env(), VoiceSettings.from_env())
    uvicorn.run(
        app,
        host=os.environ.get("HOST", DEFAULT_HOST),
        port=int(os.environ.get("PORT", DEFAULT_PORT)),
    )


if __name__ == "__main__":
    main()
