FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src src
COPY data data
RUN uv sync --locked --no-dev
COPY build/index.sqlite build/index.sqlite

ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0
CMD ["python", "-m", "vcity"]
