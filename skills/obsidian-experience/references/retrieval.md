# Local note retrieval

The helper reads the current Markdown vault, parses YAML properties, splits body text into bounded sections, and returns relevant original passages with paths and line numbers. No Obsidian plugin, open Obsidian window, or Codetrap installation is required.

## Runtime and provider

Use `uv run /absolute/path/to/scripts/retrieval.py ...`. The entrypoint declares Python 3.11–3.13 and pinned PyYAML, ONNX Runtime, Tokenizers and NumPy. uv selects/downloads a compatible Python and installs an isolated runtime on first use. These runtime downloads are additional to the model's size. A plain Python environment may install the same dependencies manually. The export scripts keep their existing runtime.

The default `--backend local` directly loads **jinaai/jina-embeddings-v2-base-zh**, a Chinese/English embedding model, with quantized ONNX weights. The two required files total **163,596,011 bytes (about 164 MB / 156 MiB)**. CPU inference needs no API key, GPU, Ollama, open Obsidian window or Codetrap installation. Memory use exceeds the model file size; no low-memory device guarantee is made. Runtime wheels determine supported OS/CPU combinations; Apple Silicon macOS is verified here, Windows/Linux and Intel Macs are not yet exercised by this skill's tests.

Read the [official model card](https://huggingface.co/jinaai/jina-embeddings-v2-base-zh) for its language coverage and mean pooling. The helper pins revision `c1ff9086a89a1123d7b5eff58055a665db4fb4b9` and both file SHA-256 values. It loads the ONNX graph and tokenizer JSON directly, never repository Python code. Attention-mask mean pooling and L2 normalization produce 768-dimensional vectors; inputs over 8192 tokens fail rather than silently truncate. See [ONNX Runtime Python](https://onnxruntime.ai/docs/get-started/with-python.html) for the runtime.

`download-model` is the explicit network step. Announce the model, download size and destination before first installation; a user request to initialize/download local retrieval already authorizes it. Ordinary search/status/index commands never download model files. Downloads use the official Hugging Face repository/CDN, show progress, retry up to three times, verify size/hash before atomic replacement and keep already verified files. An interrupted file restarts on retry; this is not byte-range resume. An offline copy can be imported with `--source-dir`. After installation, notes and queries remain on the computer. Use `uv run --offline` once the Python environment is cached to prevent uv runtime downloads too.

The shared model cache defaults to `~/.cache/obsidian-experience/models/jina-v2-base-zh/<revision>/`. `--model-dir` selects an alternate **root**; use the same root for download, status, indexing and search. Models are shared between vaults; each vault retains its own vector index. Keep caches outside the vault. `model-status` verifies installed files and reports readiness, size and location; index `status` separately reports coverage.

Ollama remains optional: pass `--backend ollama` on each index/search/status command, with optional `--model qwen3-embedding:0.6b` and `--endpoint http://127.0.0.1:11434`. This backend requires an already installed model and running service; it does not start or install Ollama. Endpoints are loopback-only with proxies/redirects disabled. [Ollama embed](https://docs.ollama.com/api/embed) uses `truncate: false`; Qwen receives its retrieval query instruction. Backend, model digest, tokenizer and pooling/instruction identities isolate the respective cached vectors. Run `index` when switching backends; existing Ollama vectors cannot be used as local Jina vectors. Keyword mode performs no embedding calls.

## First use and sharing

Share the entire `obsidian-experience` skill folder, including `scripts`, `references` and `assets`. Model files, personal notes and index caches are not part of the skill. The recipient needs uv available (or an equivalent Python environment), then can ask the agent: “初始化 Obsidian 语义检索，下载本地模型并给我的笔记建索引。” Resolve their vault from their own context, run `model-status`, `download-model`, `index`, then a sample query. Never distribute the current user's vault path or personal evaluation files as defaults.

No API credential or Ollama setup is needed. Installation still needs internet access to the Python package registry and Hugging Face; on an offline computer, provision the Python dependencies plus the verified model files in advance. `download-model --source-dir /path/to/files` expects `tokenizer.json` and `onnx/model_quantized.onnx` relative to that directory. Arbitrary third-party embedding models are not accepted by the local backend: add a tested pinned manifest/provider before exposing another choice.

## Commands

Run from the skill directory, or replace the script path with its absolute path. All commands emit UTF-8 JSON; indexing progress goes to stderr.

```sh
# First use: report model size/cache location, then download it explicitly.
uv run scripts/retrieval.py model-status
uv run scripts/retrieval.py download-model

# Optional offline file import; no network model download.
uv run scripts/retrieval.py download-model --source-dir "/path/to/model-files"

# Inspect current vault coverage and skipped notes.
uv run scripts/retrieval.py status --vault "/path/to/vault"

# Build/update a local index. Unchanged passages reuse vectors.
uv run scripts/retrieval.py index --vault "/path/to/vault"

# General note, article, conversation and historical searches.
uv run scripts/retrieval.py search "几个操作互相等着，写不进去" --vault "/path/to/vault"
uv run scripts/retrieval.py search "busy_timeout" --mode fts --vault "/path/to/vault"
uv run scripts/retrieval.py search "上次怎么处理并发写入" --mode semantic --vault "/path/to/vault"

# Only explicitly active lessons from this project or explicitly global advice.
uv run scripts/retrieval.py recall "数据库写入等待" --project atlas --module db --path src/db/connection.py --vault "/path/to/vault"

# Recreate a damaged cache. This does not rewrite notes.
uv run scripts/retrieval.py index --rebuild --vault "/path/to/vault"
```

Queries may be omitted or supplied as `-` to read stdin. Use structured arguments or safe shell quoting for user text. Search supports `--limit` (default 5, maximum 100), `--folder` (note folder relative to vault), `--type`, `--project`, `--status`, `--module`, and `--path` (source-code applicability, not a note filename). Project matching is exact against established metadata, plus `global`; do not guess or mint another identifier just to make a search match. Legacy absolute-path project properties can be supplied as-is.

## Search versus recall

- `search` intentionally includes old/draft/unclassified materials unless filtered. Missing metadata does not prevent general discovery. Current and legacy `sources`/`source`, `description`/`summary`, YAML aliases/tags and body content are searchable. H1 or filename supplies a missing title.
- `recall` requires `--project`, recognizes `type: lesson` or untyped notes in the top-level `Lessons`/`经验` folder, and returns only `status: active` with matching project/global scope. Unknown project/status never becomes an active recommendation. Path globs and module constraints apply before result limits. Missing module/path constraints mean unrestricted applicability within the stated project, not verified applicability to every situation.
- A matching superseded lesson can lead to its active replacement. Results include the traversed paths. Missing/ambiguous replacement links and cycles produce diagnostics; a target from another project cannot bypass scope filters. General history search returns the original historical record instead.
- Current user instructions, code and validated project docs outrank historical notes. Inspect concrete applicability and evidence before applying advice. For meaningful code work in an area with known saved lessons, use recall before editing; this is not a promise that the skill will automatically load in every future session.

## Ranking and returned evidence

`fts` is a local BM25-style keyword scorer, not SQLite FTS5. It preserves exact identifiers, splits URL/path components, uses CJK bigrams and expands a small Chinese/English terminology map. Title and aliases receive extra weight. `semantic` compares current passage vectors using cosine similarity. The default `--min-score` is 0.30 for the local Jina model and 0.45 for Ollama; it is a retrieval cutoff, not a calibrated probability, and needs evaluation for a different model. The local default was adjusted after the inherited 0.45 cutoff excluded top-ranked Chinese paraphrase matches scoring 0.338–0.428 in the bundled fixture. This small-fixture adjustment is not general calibration; read candidate notes and expand evaluation with actual use.

`hybrid` combines the two **note rankings** using reciprocal rank fusion (constant 60). Each note contributes once per branch, preventing many chunks of one long conversation from filling the result list. Up to two matched excerpts provide original body text and line locations. Long single lines may be split; line ranges then locate the source line containing that partial excerpt.

Each result includes `absolute_path`, vault-relative `path`, title, type, project/status, description, aliases/tags, sources, applicability, `matched_by`, ranking score/kind, `replacement_chains`, `source_sha256`, snippets and links. These are evidence candidates, not generated conclusions. Read the full note for context or exact instructions, then present an action/reading card using the collection conventions. In Codex responses use a clickable absolute filesystem link, optionally with `:start_line`, rather than copying the helper's `file://` URI. `obsidian_uri` opens the note directly in Obsidian when wanted.

## Index maintenance and diagnostic behavior

The default cache is `~/.cache/obsidian-experience/<vault-identity>/index.sqlite3`. A different `--cache-root` must be outside the vault. Each attachment-inclusion mode has a separate cache. Cache rows contain vectors, passage hashes, note paths/hashes and model profile metadata; they are not editable experience records. The cache can be deleted and reconstructed, and does not need Obsidian sync or backup.

Every query rereads current Markdown. Vectors are used only when passage hashes match. Modifications use current keyword text until indexed; deleted notes are not returned. A model change uses a different profile. `index` refreshes changed/new passages and removes deleted entries for that profile; `--rebuild` recreates the entire cache, including replacing obsolete profiles. Failed model calls or a detected vault change during indexing leave the previous usable cache intact. Recheck after concurrent vault edits; the helper has no watcher or background synchronization.

After an authorized save or revision, a local incremental `index` refresh is part of maintaining retrieval. If the local model/runtime is unavailable, finish the save, report that semantic indexing is pending, and retain keyword access. Do not silently claim all notes are semantically searchable.

Inspect diagnostics even when results are empty:

| Code | Meaning / response |
|---|---|
| `index_missing` | No cache for this model; run `index` when the local model is ready |
| `partial_index` | Some relevant current passages lack vectors; refresh and retry once |
| `semantic_unavailable` | Provider/cache/model failure; hybrid retains keyword results, semantic returns no invented fallback matches |
| `semantic_no_candidates` | No fresh vector match passed the cutoff; not proof the vault lacks an answer |
| `no_applicable_notes` | No notes matched scope/lifecycle filters; indexing cannot manufacture confirmed lessons |
| `note_skipped` | Malformed YAML, unreadable or oversized note; inspect the named file without overwriting it |
| `source_changed` | A result or replacement-chain source changed during the model call; retry |
| `replacement_missing`, `replacement_cycle` | Repair the relationship only within an authorized note-maintenance task |

The helper excludes hidden folders/files, `.assets` bundles, `node_modules` and attachment folders by default; it does not follow note/directory symlinks. `--include-attachments` includes Markdown inside attachment folders, not image/HTML/PDF content. The default per-note limit is 2 MiB, adjustable with `--max-note-mb`; skipped notes are explicit. Search is linear over the selected vault and cached vectors: suitable for a personal collection, with no large-vault latency claim.

## Verification and evaluation

```sh
# Mechanical behavior, using isolated fake vectors; never a semantic-quality claim.
uv run scripts/test_retrieval.py -v
uv run scripts/test_local_model.py -v

# Real installed embedding model over a bundled synthetic temporary vault.
uv run scripts/evaluate_retrieval.py --output /path/to/synthetic-report.json

# Curated questions about actual notes; never changes the source notes.
uv run scripts/retrieval.py evaluate --vault "/path/to/vault" --fixture /path/to/cases.json --mode hybrid
```

An evaluation fixture has `cases`, each with `query`, `expected_paths` and optional `forbidden_paths`; use `expect_empty: true` for a deliberately empty scope. Cases may set `recall`, `project`, `status`, `kind`, `module`, `path` and `folder`. Paths are vault-relative. Reports show Recall@3, MRR@5, forbidden hits, per-case diagnostics, and a fixture hash. A case passes when a relevant note occurs in its top three and no forbidden note appears in the top five; empty-scope cases require zero results. The synthetic runner compares FTS, semantic and hybrid; semantic/hybrid misses fail its exit status, while FTS is a visible baseline.

Use actual model calls to judge paraphrases and multilingual queries. Passing fake-vector tests proves filtering/cache/fusion mechanics only. Keep personal evaluation fixtures/reports local; do not publish them as generic benchmark data. No score proves that retrieved advice was useful or prevented an error.
