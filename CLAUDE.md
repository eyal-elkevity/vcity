# vcity

Voice agent for the City of Corona, CA, built for the Threefold developer take-home.
The design is in `docs/spec.md`.

## Commands

```bash
uv run ruff check          # lint
uv run ruff format         # format (CI runs --check)
uv run pyright             # strict type check
uv run pytest              # tests
```

All four must pass before every commit.

## Architecture rule

`src/vcity/domain/` is pure. No I/O, no reading the system clock, and no imports from other vcity packages.
Adapters (`config`, and later `knowledge`, `tickets`, `telephony`, `voice`) depend on the domain, never the reverse.

## Code standard

- Modern, idiomatic Python 3.12, used where it makes code clearer: typed unions with exhaustive `match` (`assert_never`), `Protocol` ports, frozen dataclasses for domain values, Pydantic only at I/O boundaries, `StrEnum`, `zoneinfo`, `asyncio.TaskGroup`.
- pyright strict, with no `# type: ignore` and no `Any` leaking out of boundaries.
- Small, single-purpose modules.
- No comments that restate the code, narrate a change, or mention tickets or TODOs. Make the code self-explanatory, and put the reasoning in the commit message. The only allowed comments are workarounds for external quirks, domain rules not visible in the code, magic numbers from an external spec, or a terse note on genuinely non-obvious code.

## Workflow

- Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`, `docs:`), each with a body explaining why.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- One branch and one PR per milestone. The PR description is the milestone's design note. The user merges.
- The repo is public, so never commit secrets or personal phone numbers.
