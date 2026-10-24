# vcity

A voice agent that answers the phone for the City of Corona, CA. It answers questions from the municipal code and the city's website and news, routes callers to the right department during business hours, and files tickets when the office is closed.

- Design: [`docs/spec.md`](docs/spec.md)

## Development

```bash
uv sync
uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest
```

Live-API tests are skipped by default. Run them with `uv run pytest -m live`. They need `.env`.

## Configuration

Copy these into `.env`, which is gitignored:

```bash
VOYAGE_API_KEY=...      # embeddings
ANTHROPIC_API_KEY=...   # relevance classifier
GAP_LOG_SALT=...        # any random string; hashes caller IDs in the gap log
```

## Knowledge index

`search_city` answers from one SQLite file, built from three sources:
- the municipal code snapshot in `data/raw/code/`
- the English pages of coronaca.gov
- the city's events feed

```bash
uv run python -m vcity.knowledge.ingest      # writes build/index.sqlite (embeddings cached in build/)
uv run python -m evals.retrieval             # hit@3, MRR and the abstention threshold sweep
```

CI downloads the published index from the `knowledge-index` release. The manual **content-refresh** workflow rebuilds the index, gates it with the retrieval eval, and republishes it.

### Refreshing the municipal code snapshot

American Legal blocks scripted access, so the snapshot is taken from a real browser session:

1. Open https://codelibrary.amlegal.com/codes/corona/latest/overview in Chrome and open the DevTools console.
2. Paste `tools/amlegal_snapshot.js` and run it with a download wrapper. It takes about 25 minutes:
   ```js
   const snapshot = await (/* paste tools/amlegal_snapshot.js here */)();
   const link = document.createElement("a");
   link.href = URL.createObjectURL(new Blob([JSON.stringify(snapshot)]));
   link.download = "corona-municipal-code.json";
   link.click();
   ```
3. Compress it and replace the committed copy:
   ```bash
   gzip -9 corona-municipal-code.json && mv corona-municipal-code.json.gz data/raw/code/
   ```
