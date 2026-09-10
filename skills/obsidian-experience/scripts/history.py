#!/usr/bin/env python3
"""Export local Codex/Claude Code conversations as Obsidian cards with images."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime

from images import image_blocks, visible_parts, Images
from reading import install_style, local_stamp, message_card


LIMITATION = (
    "Conversation text and available images only. Tool text, reasoning, and "
    "system/developer messages are excluded; no raw-log backup is created."
)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def within(path, root):
    return path == root or root in path.parents


def source_root(home, client):
    return (home / (".codex/sessions" if client == "codex" else ".claude/projects")).resolve()


def checked_source(path, root):
    if path.is_symlink() or not within(path.resolve(), root) or not path.is_file():
        raise ValueError("Source must be a regular, non-symlink file inside the selected history root.")
    return path.resolve()


def rows_from(raw):
    for number, line in enumerate(raw.decode("utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise ValueError(f"Invalid/incomplete JSONL at line {number}; wait for the writer and read again.") from error
        if not isinstance(row, dict):
            raise ValueError(f"JSONL line {number} is not an object.")
        yield number, row


def metadata(rows, client):
    result = {"session_id": None, "project": None, "started_at": None}
    for _, row in rows:
        data = row.get("payload") if client == "codex" and row.get("type") == "session_meta" else row if client == "claude" else {}
        if not isinstance(data, dict):
            continue
        for key, value in (("session_id", data.get("id" if client == "codex" else "sessionId")),
                           ("project", data.get("cwd")), ("started_at", row.get("timestamp"))):
            if result[key] is None and isinstance(value, str) and value:
                result[key] = value
    return result


def matches_project(recorded, project):
    return isinstance(recorded, str) and Path(recorded).is_absolute() and within(Path(recorded).resolve(), project)


def message_record(row, client):
    if client == "codex":
        item = row.get("payload") if row.get("type") == "response_item" else None
        return item if isinstance(item, dict) else None
    item = row.get("message") if row.get("type") in ("user", "assistant") else None
    return item if isinstance(item, dict) else None


def file_hash(path, length=None):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        remaining = length
        while remaining is None or remaining > 0:
            chunk = handle.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
            if not chunk:
                if remaining:
                    raise ValueError("Source was truncated; read again.")
                break
            hasher.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return hasher.hexdigest()


def snapshot(path, client, root, project, offset=0, limit=None, source_bytes=None, on_message=None):
    """Stream a fixed source prefix; keep only the requested text page in memory.

    source_bytes pins an earlier preview even when the live session appends more
    events. Both the byte prefix and its SHA-256 are checked before publication.
    """
    path = checked_source(path, root)
    info = {"session_id": None, "project": None, "started_at": None}
    messages, total = [], 0
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Source is not a regular file.")
        length = before.st_size if source_bytes is None else source_bytes
        if length < 0 or length > before.st_size:
            raise ValueError("Requested source prefix is unavailable; read again.")
        remaining, line = length, 0
        while remaining:
            raw = handle.readline(remaining)
            if not raw:
                raise ValueError("Source was truncated during reading.")
            remaining -= len(raw)
            line += 1
            hasher.update(raw)
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError("Expected an object")
            except ValueError as error:
                raise ValueError(f"Invalid/incomplete JSONL at line {line}; wait for the writer and read again.") from error
            current = metadata([(line, row)], client)
            for key, value in current.items():
                if info[key] is None and value is not None:
                    info[key] = value
            item = message_record(row, client)
            if item is None:
                continue
            if client == "codex" and item.get("type") != "message":
                parts = image_blocks(item.get("output")) if "call" in str(item.get("type", "")) else []
                role = "assistant"
            else:
                role = item.get("role", row.get("type"))
                if item.get("channel") == "analysis":
                    continue
                parts = visible_parts(item.get("content"))
            if role not in ("user", "assistant"):
                continue
            text = "\n".join(part["text"] for part in parts if part["type"] == "text")
            image_count = sum(part.get("type") != "text" for part in parts)
            if text or image_count:
                total += 1
                message = {"number": total, "line": line, "role": role, "images": image_count,
                           "timestamp": row.get("timestamp") if isinstance(row.get("timestamp"), str) else None,
                           "text": text}
                if on_message is not None:
                    on_message(message, parts)
                if total > offset and (limit is None or len(messages) < limit):
                    messages.append(message)
        after = os.fstat(handle.fileno())
    version = hasher.hexdigest()
    if after.st_size < length or file_hash(path, length) != version:
        raise ValueError("Source prefix changed during reading; read again.")
    if not matches_project(info["project"], project):
        raise ValueError("Recorded project is missing or outside --project; nothing exported.")
    return {**info, "session_id": info["session_id"] or path.stem, "client": client,
            "source_file": str(path), "version": version, "source_bytes": length,
            "source_grew": after.st_size > length, "messages": messages,
            "total_messages": total, "limitation": LIMITATION}


def discover(home, clients, project, limit):
    sessions, warnings = [], []
    roots = []
    for client in clients:
        root = source_root(home, client)
        roots.append(str(root))
        if not root.is_dir():
            warnings.append(f"{client}: history root unavailable: {root}")
            continue
        def walk_error(error):
            warnings.append(str(error))
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink())
            for name in sorted(files):
                if not name.endswith(".jsonl"):
                    continue
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                try:
                    checked_source(path, root)
                    # Discovery reads only a bounded header, never the whole corpus.
                    with path.open("rb") as handle:
                        head = handle.read(256 * 1024)
                    prefix = head.rsplit(b"\n", 1)[0] if len(head) == 256 * 1024 else head
                    rows = list(rows_from(b"\n".join(prefix.split(b"\n")[:32])))
                    info = metadata(rows, client)
                    if not info["project"]:
                        warnings.append(f"{path}: no project found in bounded header; not listed.")
                        continue
                    if not matches_project(info["project"], project):
                        continue
                    sessions.append({**info, "session_id": info["session_id"] or path.stem,
                                     "client": client, "source_file": str(path),
                                     "file_modified_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()})
                except (OSError, ValueError) as error:
                    warnings.append(f"{path}: {error}")
    sessions.sort(key=lambda item: (datetime.fromisoformat(item["file_modified_at"]).timestamp(), item["source_file"]), reverse=True)
    return {"sessions": sessions[:limit], "total": len(sessions), "has_more": len(sessions) > limit,
            "roots": roots, "warnings": warnings,
            "note": "Sorted by file modification time, not conversation date. Listing is not a full validity check."}


def selection_spec(data, args):
    count = data["total_messages"]
    if args.all:
        numbers, selection = None, {"mode": "all"}
    elif args.messages is not None:
        numbers = set()
        for token in args.messages.split(","):
            match = re.fullmatch(r"\s*([1-9][0-9]*)(?:-([1-9][0-9]*))?\s*", token)
            if not match:
                raise ValueError("--messages expects numbers/ranges such as 2,4-6.")
            start, end = int(match[1]), int(match[2] or match[1])
            if not 1 <= start <= end <= count:
                raise ValueError("Selected message number is out of range.")
            numbers.update(range(start, end + 1))
        selection = {"mode": "selected", "numbers": sorted(numbers)}
    else:
        number = args.excerpt_message
        if not 1 <= number <= count:
            raise ValueError("Excerpt message number is out of range.")
        if args.start is None or args.end is None or not 0 <= args.start < args.end:
            raise ValueError("Excerpt requires valid zero-based Unicode code-point --start/--end (end exclusive).")
        numbers = {number}
        selection = {"mode": "excerpt", "number": number, "start": args.start, "end": args.end,
                     "offset_unit": "unicode_code_points"}
    if args.excerpt_message is None and (args.start is not None or args.end is not None):
        raise ValueError("--start/--end require --excerpt-message.")
    if not count:
        raise ValueError("No readable messages selected; nothing exported.")
    return numbers, selection


def note_header(data, saved_count, selection, snapshot_id, title, captured_at):
    properties = {"cssclasses": ["conversation-reading"], "reading_format": "cards-v1",
                  "type": "conversation", "client": data["client"], "session_id": data["session_id"],
                  "project": data["project"], "sources": [Path(data["source_file"]).as_uri()],
                  "source_sha256": data["version"], "source_bytes": data["source_bytes"],
                  "snapshot_id": snapshot_id, "captured_at": captured_at,
                  "selection": selection, "saved_messages": saved_count, "total_readable_messages": data["total_messages"]}
    front = "\n".join(f"{key}: {encoded(value)}" for key, value in properties.items())
    client = "Codex" if data["client"] == "codex" else "Claude Code"
    return f"---\n{front}\n---\n\n# {title}\n\n{client} · {saved_count} 条消息 · 时间按导出设备本地时区显示\n\n"


def destination_folder(args):
    vault = args.vault.expanduser().resolve()
    if not vault.is_dir():
        raise ValueError("--vault must name an existing, user-selected directory.")
    folder = Path(args.folder)
    if folder.is_absolute() or ".." in folder.parts or ".obsidian" in folder.parts:
        raise ValueError("--folder must stay inside the vault and outside .obsidian.")
    destination = (vault / folder).resolve()
    if not within(destination, vault):
        raise ValueError("Destination resolves outside the vault.")
    return destination


def snapshot_identity(data, args, selection):
    roots = sorted({str(root.expanduser().resolve()) for root in args.asset_root})
    return digest(encoded([5, data["client"], data["source_file"], data["version"], selection, roots]).encode("utf-8"))


@contextmanager
def export_lock(destination, snapshot_id):
    """Serialize identity checks/publication even when concurrent titles differ."""
    lock = destination / f".history-export-{snapshot_id}.lock"
    deadline = time.monotonic() + 10
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError(f"Another export holds {lock}. Retry after it finishes; if it crashed, inspect the lock before removing it.")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.rmdir()


def readable_filename_title(title):
    # Bound bytes as well as characters for Chinese names on common filesystems.
    value = unicodedata.normalize("NFC", title)
    value = "".join(char for char in value if not unicodedata.category(char).startswith("C"))
    value = re.sub(r'[<>:"/\\|?*\[\]#^]', "-", value)
    value = " ".join(value.split()).strip(" .-")
    return value.encode("utf-8")[:140].decode("utf-8", errors="ignore").rstrip(" .-") or "conversation"


def snapshot_path(destination, data, snapshot_id, title):
    slug = re.sub(r"[^\w-]", "-", data["session_id"], flags=re.UNICODE).strip("-")[:60] or "session"
    legacy = destination / f"{data['client']}-{slug}-{snapshot_id[:16]}.md"
    # The suffix survives a changed --title and finds earlier readable exports.
    matches = set(destination.glob(f"* -- {data['client']}-{snapshot_id[:16]}.md"))
    if legacy.exists() or legacy.is_symlink():
        matches.add(legacy)
    if len(matches) > 1:
        raise ValueError("Multiple exports have this snapshot identity; resolve the existing copies before saving. Nothing overwritten.")
    if matches:
        return matches.pop()
    if not title:
        return legacy
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    return destination / f"{day} — {readable_filename_title(title)} -- {data['client']}-{snapshot_id[:16]}.md"


def export_note(data, args):
    if data["version"] != args.expect_version:
        raise ValueError("Source changed since preview. Read again and reselect; nothing exported.")
    _, selection = selection_spec(data, args)
    destination = destination_folder(args)
    destination.mkdir(parents=True, exist_ok=True)
    with export_lock(destination, snapshot_identity(data, args, selection)):
        return export_locked_note(data, args)


def export_locked_note(data, args):
    if data["version"] != args.expect_version:
        raise ValueError("Source changed since preview. Read again and reselect; nothing exported.")
    numbers, selection = selection_spec(data, args)
    destination = destination_folder(args)
    roots = sorted({str(root.expanduser().resolve()) for root in args.asset_root})
    snapshot_id = snapshot_identity(data, args, selection)
    supplied_title = " ".join((args.title or "").split())
    path = snapshot_path(destination, data, snapshot_id, supplied_title)
    title = supplied_title or f"{data['client']} · {data['session_id']}"

    assets_path = path.with_suffix(".assets")
    captured_at = datetime.now().astimezone().isoformat()

    def existing_capture():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Existing note is not a regular file; left untouched.")
        with path.open("r", encoding="utf-8", newline="") as handle:
            front = handle.read(64 * 1024).split("\n---\n", 1)[0]
        match = re.search(r"^captured_at: (.+)$", front, re.MULTILINE)
        if not match:
            raise ValueError("Existing note differs; left untouched.")
        return json.loads(match[1])

    if path.exists() or path.is_symlink():
        captured_at = existing_capture()
    saved_count = data["total_messages"] if numbers is None else len(numbers)
    destination.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=destination, prefix=".history-export-"))
    image_store = Images(stage, assets_path.name, Path(data["source_file"]), args.project.expanduser().resolve(), [Path(root) for root in roots])
    duplicate, created_assets = False, False
    try:
        body = stage / "body.md"
        previous_date = None
        with body.open("wb") as handle:
            def save_message(message, parts):
                nonlocal previous_date
                if numbers is not None and message["number"] not in numbers:
                    return
                if selection["mode"] == "excerpt":
                    if args.end > len(message["text"]):
                        raise ValueError("Excerpt end is outside the selected text; nothing exported.")
                    content = message["text"][args.start:args.end]
                else:
                    content = image_store.render(parts, message["line"])
                day, _ = local_stamp(message["timestamp"])
                if day and day != previous_date:
                    handle.write(f"## {day}\n\n".encode("utf-8"))
                    previous_date = day
                handle.write(message_card(message, content, data["client"], collapse_context=selection["mode"] != "excerpt").encode("utf-8"))
            checked = snapshot(Path(data["source_file"]), data["client"], source_root(args.home.expanduser().resolve(), data["client"]),
                               args.project.expanduser().resolve(), limit=0, source_bytes=data["source_bytes"], on_message=save_message)
        if checked["version"] != data["version"]:
            raise ValueError("Source prefix changed during export; nothing published.")
        expected_assets = {item["file"]: item["sha256"] for item in image_store.records if item["status"] == "saved"}
        temporary_note = stage / "note.md"

        def compose(capture_time):
            with temporary_note.open("wb") as handle, body.open("rb") as content:
                handle.write(note_header(data, saved_count, selection, snapshot_id, title, capture_time).encode("utf-8"))
                shutil.copyfileobj(content, handle)

        def verify_assets():
            if assets_path.is_symlink() or not assets_path.is_dir():
                raise ValueError("Existing image folder differs; left untouched.")
            actual = list(assets_path.iterdir())
            if {entry.name for entry in actual} != set(expected_assets):
                raise ValueError("Existing image folder differs; left untouched.")
            for entry in actual:
                if entry.is_symlink() or not entry.is_file() or file_hash(entry) != expected_assets[entry.name]:
                    raise ValueError("Existing image was modified; left untouched.")

        def verify_note():
            compose(existing_capture())
            if file_hash(path) != file_hash(temporary_note):
                raise ValueError("Existing note differs (possibly manually edited); left untouched.")
            if expected_assets:
                verify_assets()

        compose(captured_at)
        if path.exists() or path.is_symlink():
            verify_note()
            duplicate = True
        else:
            if expected_assets:
                if assets_path.exists() or assets_path.is_symlink():
                    verify_assets()
                else:
                    try:
                        (stage / "assets").rename(assets_path)
                        created_assets = True
                    except OSError:
                        if not assets_path.exists():
                            raise
                        verify_assets()
            try:
                os.link(temporary_note, path)
            except FileExistsError:
                verify_note()
                duplicate = True
            if file_hash(path) != file_hash(temporary_note):
                raise ValueError("Saved note failed readback verification.")
    finally:
        shutil.rmtree(stage)
        if created_assets and not path.exists():
            shutil.rmtree(assets_path)
    missing = [item for item in image_store.records if item["status"] == "unavailable"]
    return {"path": str(path), "duplicate": duplicate, "saved_messages": saved_count,
            "image_files": len(expected_assets), "remote_images": sum(item["status"] == "remote" for item in image_store.records),
            "unavailable_images": len(missing), "warnings": missing[:20],
            "total_readable_messages": data["total_messages"], "selection": selection,
            "source_sha256": data["version"], "limitation": LIMITATION}


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return number


def registered_vaults(config=None):
    if config is None:
        if sys.platform == "darwin":
            config = Path.home() / "Library/Application Support/obsidian/obsidian.json"
        elif os.name == "nt":
            config = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "obsidian/obsidian.json"
        else:
            config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "obsidian/obsidian.json"
    if not config.is_file():
        return {"vaults": [], "config": str(config), "warnings": ["Obsidian registry not found; request the vault path."]}
    registry = json.loads(config.read_text(encoding="utf-8"))
    vaults = []
    for identifier, value in registry.get("vaults", {}).items():
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            continue
        path = Path(value["path"]).expanduser()
        if path.is_absolute():
            vaults.append({"id": identifier, "name": path.name, "path": str(path),
                           "exists": path.is_dir(), "open": value.get("open") is True})
    return {"vaults": vaults, "config": str(config), "warnings": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    vault_command = commands.add_parser("vaults", help="List Obsidian's registered paths without reading notes.")
    vault_command.add_argument("--config", type=Path, help="Explicit alternate registry, for testing or a known configuration.")
    style_command = commands.add_parser("style", help="Enable conversation-only Obsidian card styling; preserve existing CSS/settings.")
    style_command.add_argument("--vault", type=Path, required=True)
    for command in ("list", "read", "export"):
        sub = commands.add_parser(command)
        sub.add_argument("--home", type=Path, default=Path.home(), help="History home; defaults to the user's home. Override for fixtures or another approved home.")
        sub.add_argument("--project", type=Path, required=True, help="Recorded working directory must be this project or a descendant.")
        sub.add_argument("--client", choices=["codex", "claude", "both"] if command == "list" else ["codex", "claude"], default="both" if command == "list" else None, required=command != "list")
        if command == "list":
            sub.add_argument("--limit", type=positive, default=20, help="Maximum metadata rows; increase when has_more is true.")
        else:
            sub.add_argument("--source", type=Path, required=True, help="JSONL path returned by list.")
            sub.add_argument("--source-bytes", type=positive, help="Pin the byte prefix returned by read, allowing new messages to append without changing this save.")
        if command == "read":
            sub.add_argument("--offset", type=int, default=0, help="Zero-based message pagination offset.")
            sub.add_argument("--limit", type=positive, default=20)
        if command == "export":
            sub.add_argument("--expect-version", required=True, help="SHA-256 version returned by read.")
            sub.add_argument("--vault", type=Path, required=True)
            sub.add_argument("--folder", default="Conversations", help="Relative vault folder; match the user's convention.")
            sub.add_argument("--title", help="Readable note title and new filename topic. Uses local capture date and a snapshot suffix; partial exports never derive titles from unselected text.")
            sub.add_argument("--asset-root", type=Path, action="append", default=[], help="Additional approved directory containing explicitly referenced local attachments; may repeat.")
            mode = sub.add_mutually_exclusive_group(required=True)
            mode.add_argument("--all", action="store_true")
            mode.add_argument("--messages", help="One-based message numbers, e.g. 2,4-6; saved in original order.")
            mode.add_argument("--excerpt-message", type=positive, help="One-based message number for an exact text range.")
            sub.add_argument("--start", type=int)
            sub.add_argument("--end", type=int)
    args = parser.parse_args()
    if args.command == "style":
        print(json.dumps(install_style(args.vault), ensure_ascii=False, indent=2))
        return
    if args.command == "vaults":
        print(json.dumps(registered_vaults(args.config), ensure_ascii=False, indent=2))
        return
    project, home = args.project.expanduser().resolve(), args.home.expanduser().resolve()
    if args.command == "list":
        result = discover(home, ["codex", "claude"] if args.client == "both" else [args.client], project, args.limit)
    else:
        if args.command == "read" and args.offset < 0:
            raise ValueError("--offset must be nonnegative.")
        data = snapshot(args.source.expanduser(), args.client, source_root(home, args.client), project,
                        offset=args.offset if args.command == "read" else 0,
                        limit=args.limit if args.command == "read" else 0, source_bytes=args.source_bytes)
        if args.command == "read":
            result = {**data,
                      "offset": args.offset, "has_more": args.offset + args.limit < data["total_messages"]}
        else:
            result = export_note(data, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # JSON remains UTF-8 even when Windows pipes use a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    try:
        main()
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
