# Conversation export reference

Use Python 3.9+ and the standard library. Run from the skill folder or use the script's absolute path. Commands return UTF-8 JSON; errors use stderr and a nonzero exit code.

## Read and save

```sh
python3 scripts/history.py vaults
python3 scripts/history.py list --project "/path/to/project"
python3 scripts/history.py read --client codex --project "/path/to/project" --source "/path/returned/by/list.jsonl"
python3 scripts/history.py style --vault "/path/to/vault"
python3 scripts/history.py export --client codex --project "/path/to/project" --source "/path/returned/by/list.jsonl" --source-bytes BYTES_FROM_READ --expect-version "VERSION_FROM_READ" --vault "/path/to/vault" --folder "Conversations" --all
```

- `vaults` reads registered paths without reading notes. `--config` selects a known alternate registry.
- `list` supports `--client codex`, `claude`, or `both` (default). It reads bounded headers and sorts by file modification time. Increase `--limit` (default 20) when `has_more` is true. Use the recorded project and session metadata to identify the source.
- `read` returns numbered text, structured-image counts, `version` and `source_bytes`, without printing base64. For more pages, use `--offset 20 --limit 20 --source-bytes BYTES_FROM_READ`; compare each page's version to the initial one.
- `export` requires the preview's actual hash and byte prefix. Later appends are allowed; rewrites/truncation require rereading. Use `--client claude` for Claude Code, an existing vault, and a meaningful `--title` drawn only from the saved scope.

## Names and repeat saves

With `--title "codetrap — 笔记管理讨论"`, a new note uses `YYYY-MM-DD — codetrap — 笔记管理讨论 -- codex-<snapshot>.md`. The date is the device-local capture date. Filename characters are sanitized and the topic is bounded to 140 UTF-8 bytes; the complete supplied title remains in H1. Without a title, the legacy ID-based naming remains available.

The snapshot suffix identifies the pinned source and selection. Retries in the same destination folder reuse readable or legacy paths, including across dates. Changing `--title` is not a rename operation: conflicting note content is preserved and reported rather than saved as another copy. Use the original title for exact retries. Multiple existing paths for the same identity are reported for resolution.

Exports serialize the identity check and publication with a temporary per-snapshot directory lock. Concurrent retries wait up to ten seconds; a timeout reports the lock path. A crashed process may leave a lock: establish that no export owns it before manually removing that exact empty directory. Locks are removed on normal completion, including handled failures.

The helper does not search other folders or automatically repair manually renamed notes and image bundles. Locate moved material before exporting and reuse its canonical location. Never change a conflict's source/selection/folder merely to obtain another filename.

History roots are `~/.codex/sessions` and `~/.claude/projects`, scoped by recorded working directory. `--home` selects another approved home or test fixture. Custom roots, archived/cloud histories, forks and subagent files are not automatically included. Reads stream without a total file-size cap; memory depends on the largest record/image. Invalid JSONL fails without publishing a partial note.

## Selection and output

Replace `--all` with one of:

- `--messages "2,4-6"`: selected messages and their pictures in original order.
- `--excerpt-message 2 --start 0 --end 12`: exact text only. Offsets are zero-based, end-exclusive Unicode code points (Python slicing, not JavaScript UTF-16). Verify the substring before saving.

Output is one Markdown note with message cards plus a sibling `<note-name>.assets` folder for deduplicated pictures. Adjacent text blocks join with a newline. Tool-result images may appear, but tool text, reasoning, system/developer messages and raw logs are excluded. This is a reading copy, not a restorable client session.

Source metadata remains in YAML properties. Retries reuse unchanged notes/assets; conflicting edits are preserved and reported. Schema changes may create a distinct snapshot filename; no existing note is automatically migrated.

## Card style

`style` installs [conversation-reading.css](../assets/conversation-reading.css) if absent and enables it in the vault. Only notes with `cssclasses: [conversation-reading]` are styled, including hidden metadata in reading view. Other settings and existing custom CSS are preserved. If `matches_bundled_style` is false, inspect the customization before promising an exact match. Refresh Obsidian's view if it has not noticed the snippet.

### Reading-view verification

Source/editing mode may show literal `>`, `[!success]`, block IDs and table delimiters even when the export is valid. Check the active tab's mode; use its “switch to reading view” control (macOS default Cmd+E) for visual verification, without changing the vault-wide editing preference. When UI access is available, inspect a user/assistant card, a table or code block, a picture and a collapsed environment block in the saved note. If UI switching or inspection is unavailable, explain how to open reading view and report only Markdown/link checks, not verified visual appearance.

Leading `in-app-browser-context` blocks (including attributes) and AGENTS project headers are retained in folded context cards. User requests, quoted examples and exact excerpts must remain unchanged; do not fold an entire mixed message just because it contains environment text. When repairing an existing export, pin its original source prefix and verify the previous rendering matches before replacement; preserve note identity, capture time and attachment paths. Do not produce a second note to bypass an edited-note conflict.

## Images

Embedded base64/data URIs and local images are copied. Local files must be inside the project, source-log directory, or an explicit `--asset-root "/approved/image/directory"` (repeatable); do not broaden roots to the whole home merely to resolve one image. Missing/unsupported images get a placeholder and details in the command result. HTTP(S) images remain external links, not offline copies. Markdown image references are localized; fenced/inline code examples stay unchanged. No image scripts are executed.

## Windows and WSL

On Windows, replace `python3` with an available Python 3.9+ command such as `py -3` or `python`. Use native Windows paths for all path arguments. Drive paths (`C:Pictures图 1.png`, `C:/Pictures/图 1.png`) and `file:///C:/...` image URIs are supported; ambiguous `C:photo.png` paths are rejected. The vault registry is under `%APPDATA%obsidian`.

Run in the same environment as the history: native Python for Windows histories, WSL Python for WSL histories. In WSL, explicitly use a verified vault mount such as `/mnt/c/Users/Alice/Documents/Obsidian Vault`; Windows drive paths and mount mappings are not converted automatically. The destination filesystem must support hard links, for example NTFS.

## Maintenance

Run `python3 -B scripts/test_history.py -v` (Windows: `py -3 -B scripts/test_history.py -v`). Fixtures use temporary histories/vaults. Windows omits Unix RSS measurement and skips the symlink test only when creation privileges are unavailable. Path tests do not replace a native Windows end-to-end run.
