import argparse
import asyncio
import contextlib
import sys
import uuid
from collections.abc import Sequence

from vcity.agent.conversation import AGENT_MODEL, Conversation
from vcity.agent.services import open_services
from vcity.agent.tools import CallContext
from vcity.settings import AppSettings

PROMPT = "you> "


async def chat(
    settings: AppSettings, *, model: str, file_to_linear: bool, show_tools: bool
) -> None:
    async with open_services(settings, file_to_linear=file_to_linear) as services:
        conversation = Conversation(
            services.anthropic,
            services.toolbox(),
            services.system,
            CallContext(f"CHAT-{uuid.uuid4().hex[:8]}", None),
            services.clock,
            model=model,
        )
        while (line := await asyncio.to_thread(_read)) is not None:
            if not line.strip():
                continue
            turn = await conversation.reply(line)
            if show_tools:
                for call in turn.tool_calls:
                    sys.stderr.write(f"  [{call.name} {call.input}]\n")
            sys.stdout.write(f"city> {turn.text}\n")


def _read() -> str | None:
    try:
        return input(PROMPT)
    except EOFError:
        return None


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Talk to the Corona agent in text.")
    parser.add_argument("--model", default=AGENT_MODEL)
    parser.add_argument(
        "--linear", action="store_true", help="file tickets in Linear instead of a mock"
    )
    parser.add_argument("--tools", action="store_true", help="show tool calls on stderr")
    args = parser.parse_args(argv)
    settings = AppSettings.from_env()
    if args.linear and settings.linear_api_key is None:
        sys.exit("--linear needs LINEAR_API_KEY")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            chat(settings, model=args.model, file_to_linear=args.linear, show_tools=args.tools)
        )


if __name__ == "__main__":
    main()
