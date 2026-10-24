# vcity

A voice agent that answers the phone for the City of Corona, CA. It answers questions from the municipal code and the city's website and news, routes callers to the right department during business hours, and files tickets when the office is closed.

- Design: [`docs/spec.md`](docs/spec.md)

## Development

```bash
uv sync
uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest
```
