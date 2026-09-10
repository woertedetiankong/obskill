"""Obsidian conversation cards and their note-scoped reading style."""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile


def local_stamp(value):
    if not value:
        return "", ""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone()
        return stamp.strftime("%Y-%m-%d"), stamp.strftime("%H:%M")
    except ValueError:
        return "", ""


def context_prefix(text):
    """Separate recognized leading context blocks without hiding the user's request."""
    position = 0
    while position < len(text):
        rest = text[position:]
        header = re.match(r"\s*# AGENTS\.md instructions[^\S\n]*(?:\n|$)", rest)
        if header:
            position += header.end()
            continue
        block = re.match(r"\s*<(recommended_plugins|environment_context|skill|skills_instructions|INSTRUCTIONS)>.*?</\1>\s*", rest, re.DOTALL)
        if not block:
            break
        position += block.end()
    return text[:position], text[position:]


def quoted(text):
    # Split only at LF; preserve CRLF, Unicode separators and original whitespace.
    return "> " + text.replace("\n", "\n> ")


def card(kind, label, content, folded=False):
    if folded:
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", content)), default=3))
        content = fence + "text\n" + content + "\n" + fence
    return f"> [!{kind}]{'-' if folded else ''} {label}\n>\n{quoted(content)}\n\n"


def message_card(message, content, client, *, collapse_context=True):
    _, clock = local_stamp(message.get("timestamp"))
    suffix = f" · {message['number']:02d}" + (f" · {clock}" if clock else "")
    context, body = context_prefix(content) if collapse_context and message["role"] == "user" else ("", content)
    rendered = card("info", "环境 / Skill 原文" + suffix, context, folded=True) if context else ""
    if body or not context:
        label = "你" if message["role"] == "user" else "Codex" if client == "codex" else "Claude"
        kind = "question" if message["role"] == "user" else "success"
        rendered += card(kind, label + suffix, body)
    return rendered + f"^message-{message['number']:02d}\n\n"


def install_style(vault):
    """Enable the bundled snippet, preserving other settings and existing user CSS."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ValueError("--vault must name an existing directory.")
    config = vault / ".obsidian" / "appearance.json"
    snippet = vault / ".obsidian" / "snippets" / "conversation-reading.css"
    for path in (config, snippet):
        if vault not in path.resolve().parents:
            raise ValueError("Obsidian style path resolves outside the vault.")
    before = config.read_bytes() if config.exists() else None
    appearance = json.loads(before) if before is not None else {}
    if not isinstance(appearance, dict):
        raise ValueError("Obsidian appearance settings must be an object; left untouched.")
    enabled = appearance.get("enabledCssSnippets", [])
    if not isinstance(enabled, list) or not all(isinstance(name, str) for name in enabled):
        raise ValueError("Invalid enabledCssSnippets; settings left untouched.")
    bundled = (Path(__file__).resolve().parent.parent / "assets/conversation-reading.css").read_bytes()
    snippet.parent.mkdir(parents=True, exist_ok=True)
    try:
        with snippet.open("xb") as handle:
            handle.write(bundled)
    except FileExistsError:
        pass
    if "conversation-reading" not in enabled:
        appearance["enabledCssSnippets"] = enabled + ["conversation-reading"]
        fd, temporary = tempfile.mkstemp(dir=config.parent, prefix=".appearance-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(appearance, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            if (config.read_bytes() if config.exists() else None) != before:
                raise ValueError("Obsidian appearance settings changed; retry without overwriting them.")
            os.replace(temporary, config)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"snippet": str(snippet), "enabled": True, "matches_bundled_style": snippet.read_bytes() == bundled}
