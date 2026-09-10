# Readable notes and long-term organization

Use these conventions when saving, finding or organizing material. They are defaults for new notes, not permission to migrate the vault. Inspect folder names and a few relevant note headers first; preserve established field names, project identities, tags, human edits and attachment paths. Do not scan unrelated personal content to choose a layout.

## Reading experience

- Use one descriptive H1, a short orientation paragraph, and H2 sections arranged for the reader's task. Prefer short paragraphs and lists for actual steps. A short bookmark does not need a long template.
- Use native Markdown and Obsidian callouts: `[!abstract]` for a brief orientation, `[!tip]` for an actionable takeaway, `[!warning]` for an applicable limitation, and `[!quote]-` for optional long evidence. Keep the main conclusion and applicability visible. Do not turn every paragraph into a card or use color alone to convey meaning.
- Put URLs, capture times and machine identifiers in properties or a compact source section. Keep a visible human-readable source link and lesson status. Syntax-highlight code; use narrow comparison tables only when they help. Avoid wide tables, excessive emoji and ornamental separators.
- Preserve source text, code, image order and requested scope. A reading introduction or AI summary is separate and explicitly labeled; do not insert advice into an exact excerpt or rewrite a saved conversation as a summary.
- Long original articles retain their original headings. Add an optional local contents list only when it helps; do not bury the entire article in a collapsed block. Conversation exports retain the bundled blue/green cards and folded environment blocks.
- Render SVG inline and link HTML with a clear opening instruction. Put attachment entry links near the top of companion notes, then list editable sources below. Preserve the working relative asset tree.
- Match the user's language. Use the local capture date/time and an offset or known timezone; Chinese content does not imply China time.

## Folder and filename conventions

Reuse the chosen vault's current folders. When no category convention exists, use this shallow structure, creating only folders needed by the current save:

| Material | Default folder | Readable filename example |
|---|---|---|
| Reusable coding lesson | `Lessons/<established-project-name>/` | `SQLite — 并发写入时避免长事务.md` |
| Explicit cross-project lesson | `Lessons/global/` | `HTTP — 重试前确认请求幂等性.md` |
| Article, excerpt or reading note | `Articles/` | `Looped Transformers — 原文摘录.md` |
| Conversation snapshot | `Conversations/` | `2026-09-10 — codetrap — 笔记管理讨论 -- codex-<snapshot>.md` |
| Editable SVG/HTML companion note | Existing collection or `Works/` | `Looped Transformers — 互动课堂.md` |
| Non-conversation attachment bundle | Existing attachment location or `Attachments/<work-slug>/` | `looped-transformers/index.html` |

The examples are naming patterns, not facts or active advice to save. Existing root-level works can stay there. Do not create parallel Chinese/English folder trees, date partitions, or elaborate taxonomies by default. Use project subfolders only when project ownership is clear; a source's project does not make its advice globally applicable.

- Put the technology/topic and the concrete problem or purpose in the filename. Avoid `笔记`, `总结`, `未命名`, `final-v2` and identifiers alone. Use one principal subject per lesson; retain a multi-topic original as source material.
- Evergreen lessons and articles use topic-based names. Conversations/logs use a date because the event matters. The export helper's filename date is the local **capture date**, not a claim about when every message occurred. Keep ordinary revision dates in properties rather than renaming on every edit.
- Prefer readable Chinese with canonical API/product names retained. Avoid filesystem-reserved characters, trailing dots/spaces, and Markdown-link delimiters in new filenames. Keep names short enough for attachment paths and cross-device use.
- For a genuinely different item with the same title, add a meaningful qualifier such as project, source author or source publication date. For an equivalent item, use the existing note or report a conflict; do not make a numbered duplicate.
- The conversation helper adds a snapshot suffix for identity and uses `--title` for the readable portion. Pass a topic/project title without adding a capture-date prefix yourself. It reuses existing exports in the requested folder, including old ID-based filenames. Keep legacy names until a rename/migration is requested.
- Preserve the helper's sibling `.assets` directories. Do not relocate them into the general attachment folder or rename exported files after saving merely for aesthetics.

## Searchable properties

For new authored notes, use the following small set when the values are known. Reuse an existing equivalent property rather than writing conflicting parallel fields. Conversation exports keep their helper-owned provenance schema; do not post-process its YAML or body just to match this table, because that breaks byte-for-byte retry checks.

| Property | Purpose |
|---|---|
| `title` | Human-readable title matching the note's H1 |
| `type` | `lesson`, `article`, `excerpt`, `reading-note`, or the existing work type |
| `description` | One or two factual sentences describing the question answered and scope |
| `aliases` | A few likely alternate names, Chinese/English terms, or previous titles |
| `tags` | A few existing technical/topic tags; avoid inventing synonymous variants |
| `project` | Established project identity; required for lessons, `global` only when explicitly intended |
| `status` | Lesson lifecycle: `draft`, `active`, `superseded`, `archived` |
| `sources` | Actual URLs or links to saved source notes; otherwise an honest source description |
| `created`, `updated` | Local ISO timestamps with offset, preserving `created` during revision |

For applicability, use optional `path_globs` and `module` when supported by the evidence. Human-readable conditions belong in the body. Treat versions and temporary workarounds as reasons to recheck; age alone does not prove a lesson wrong. Keep raw error messages/API identifiers searchable. Do not invent aliases, metadata facts, validation dates or applicability to fill a template.

Search legacy `source`/`captured` and helper `captured_at` fields too. Quote YAML strings containing special syntax and use real YAML lists. For Obsidian properties containing internal links, quote each link. New note templates are in [lesson template](../assets/lesson-template.md) and [reading-note template](../assets/reading-note-template.md); substitute actual values and remove optional empty sections. They are patterns, not ready-to-save records.

## Saving and maintaining

1. Search for the canonical item before saving: source URL or session/snapshot identity, filename/title/aliases, then overlapping topic and scope. Separate full source, excerpt and derived lesson intentionally and link them; do not confuse them with equivalent duplicates.
2. Reuse the canonical path. A request to resave identical material should return it. If the user edited it, preserve those edits; update only within the requested scope. For conversation retries, use the original title and folder. If a snapshot was moved or renamed, locate it first and inspect its links; do not blindly export to the old destination.
3. Record the actual source and verification evidence. Lessons should say when they apply, what to avoid, what to do and how the result was checked. Do not promote broad reminders or unverified inferences to active advice.
4. For a correction, update in place when it is the same lesson. For a distinct approved replacement, link the old and new notes and set `superseded_by` on the old one after the new note is saved. Keep obsolete notes out of recommended actions, while allowing historical search to find them. Repeated source captures are not independent evidence.
5. Reuse an existing project/topic index when it is part of the collection's normal save workflow. Create a short Markdown index only when several related notes need navigation; list canonical links and one-line descriptions, not copies of their bodies. No one-index-per-note policy or global index rewrite on each save.
6. A request to organize existing notes authorizes the relevant moves/renames and link repairs. Reread first, inspect incoming links and attachment references, preserve previous titles as aliases when appropriate, and verify the affected links afterward. Ordinary saving does not authorize a vault-wide cleanup. Do not promise that external links/bookmarks will follow a rename.

## Finding and using saved content

Start with current project, technology, file/module, error text and the user's own description. Search filenames, aliases and descriptions first, then body text and sources when necessary; metadata-only misses are not proof of absence. Expand common alternate terms and Chinese/English spellings without replacing exact identifiers. Explicit vault-wide/article/history searches should not be silently restricted to active lessons.

For recommended coding actions, read the top plausible candidates, check `project`/`status`/applicability, follow replacement links and compare with current code/docs. Unknown status or scope is unconfirmed. Similarity alone does not establish equivalence, and current instructions outrank historical notes.

Return a compact card: **why relevant → avoid → do instead → evidence/source link**. For an article, use **what it covers → relevant passage → source link**. Open the full note when exact code, context or conflicting advice matters. Keep these cards in the answer unless the user asks to save them.

Use the [retrieval helper](retrieval.md) for keyword, local semantic or hybrid search. Its cache is disposable and reconstructed from Markdown; current note text, scope, status and replacement links remain authoritative. Report the actual `matched_by` branches and diagnostics. Keyword fallback must not be reported as a semantic match. A successful recall is evidence of retrieval, not proof that the advice is correct or useful.

## Verification

Read back the saved file. Check H1/title agreement, meaningful filename, actual aliases/tags, valid source and attachment links, unchanged source scope, and visible lesson status. For existing notes, verify preserved edits and repaired links. When rendering is available, inspect heading hierarchy, callouts, code blocks, long links/images and narrow-width reading; otherwise report Markdown/link checks only. Do not claim Obsidian visual QA from a plain file read.
