import os

import uvicorn

from vcity.app import create_app
from vcity.settings import AppSettings, VoiceSettings

DEFAULT_PORT = 8080


def main() -> None:
    app = create_app(AppSettings.from_env(), VoiceSettings.from_env())
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", DEFAULT_PORT)))  # noqa: S104


if __name__ == "__main__":
    main()
