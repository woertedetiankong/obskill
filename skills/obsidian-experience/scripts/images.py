"""Localize conversation images without collecting tool text or reasoning."""

import base64
import hashlib
import json
import mimetypes
from nturl2path import url2pathname as windows_url2pathname
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote, unquote, unquote_to_bytes, urlparse


IMAGE_TYPES = ("image", "input_image", "image_url")
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
              "image/svg+xml": ".svg", "image/avif": ".avif", "image/bmp": ".bmp", "image/tiff": ".tiff"}
IMAGE_EXTENSIONS = set(EXTENSIONS.values()) | {".jpeg", ".tif"}
MARKDOWN_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\s*\)")


def local_image_path(reference, *, windows):
    """Decode a local image reference using the executing filesystem's syntax."""
    # Parse drive paths before URLs: urlparse treats C: as a URI scheme.
    if re.match(r"^[A-Za-z]:", reference):
        if not windows:
            raise ValueError("Windows image path requires Windows Python; in WSL use its mounted path.")
        if not re.match(r"^[A-Za-z]:[\\/]", reference):
            raise ValueError("Drive-relative image paths are ambiguous; use an absolute path.")
        return reference
    uri = urlparse(reference)
    if uri.scheme and uri.scheme != "file":
        raise ValueError("Image has no usable local path.")
    if uri.scheme == "file":
        if uri.netloc.lower() not in ("", "localhost") or uri.path.startswith("//"):
            raise ValueError("Non-local file URI.")
        if not windows and re.match(r"^/?[A-Za-z]:", unquote(uri.path)):
            raise ValueError("Windows image URI requires Windows Python; in WSL use its mounted path.")
        return windows_url2pathname(uri.path) if windows else unquote(uri.path)
    return unquote(reference)


def image_blocks(value):
    """Extract structured images from tool results; never expose their text."""
    if isinstance(value, str):
        if not value.lstrip().startswith(("{", "[")):
            return []
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if isinstance(value, list):
        return [image for item in value for image in image_blocks(item)]
    if not isinstance(value, dict):
        return []
    if value.get("type") in IMAGE_TYPES:
        return [value]
    if value.get("type") in ("thinking", "reasoning", "redacted_thinking", "tool_use"):
        return []
    return [image for key in ("content", "output", "result") if key in value for image in image_blocks(value[key])]


def visible_parts(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("text", "input_text", "output_text") and isinstance(block.get("text"), str):
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") in IMAGE_TYPES:
            parts.append(block)
        elif block.get("type") == "tool_result":
            parts.extend(image_blocks(block.get("content")))
    return parts


def sha256(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class Images:
    def __init__(self, stage, prefix, source, project, extra_roots):
        self.stage, self.prefix, self.source, self.project = stage, prefix, source, project
        self.roots = [project.resolve(), source.parent.resolve()] + [root.resolve() for root in extra_roots]
        self.records, self.cache = [], {}

    def capture(self, reference, line, mime=None):
        key = hashlib.sha256(json.dumps([reference, mime], ensure_ascii=False).encode()).hexdigest()
        if key in self.cache:
            record = {**self.cache[key], "line": line}
            self.records.append(record)
            return record
        label = "embedded image"
        try:
            data, local = None, None
            if isinstance(reference, dict) and reference.get("type") == "base64":
                mime = reference.get("media_type") or mime
                data = base64.b64decode(re.sub(r"\s+", "", reference.get("data", "")), validate=True)
            elif isinstance(reference, str) and reference.startswith("data:"):
                header, payload = reference.split(",", 1)
                mime = header[5:].split(";", 1)[0] or mime
                data = base64.b64decode(re.sub(r"\s+", "", payload), validate=True) if ";base64" in header else unquote_to_bytes(payload)
            elif isinstance(reference, str):
                label = reference
                uri = urlparse(reference)
                if uri.scheme in ("http", "https"):
                    record = {"status": "remote", "reference": reference}
                    self.cache[key] = record
                    self.records.append({**record, "line": line})
                    return record
                candidate = Path(local_image_path(reference, windows=os.name == "nt"))
                candidates = [candidate] if candidate.is_absolute() else [self.project / candidate, self.source.parent / candidate]
                for candidate in candidates:
                    resolved = candidate.resolve()
                    if not candidate.is_symlink() and any(resolved == root or root in resolved.parents for root in self.roots) and resolved.is_file():
                        local = resolved
                        break
                if local is None:
                    raise ValueError("Image missing or outside approved image roots.")
                mime = mime or mimetypes.guess_type(str(local))[0]
            else:
                raise ValueError("No image bytes or usable path in the record.")
            extension = EXTENSIONS.get(mime) or (local.suffix.lower() if local else "")
            if extension not in IMAGE_EXTENSIONS:
                raise ValueError("Image format is unavailable or unsupported.")
            assets = self.stage / "assets"
            assets.mkdir(exist_ok=True)
            temporary = assets / ".copying"
            try:
                if local:
                    before = local.stat()
                    shutil.copyfile(local, temporary)
                    after = local.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("Image changed during copying.")
                else:
                    temporary.write_bytes(data)
                fingerprint = sha256(temporary)
                saved = assets / (fingerprint + extension)
                if not saved.exists():
                    temporary.rename(saved)
                record = {"status": "saved", "reference": label, "file": saved.name, "sha256": fingerprint}
            finally:
                temporary.unlink(missing_ok=True)
        except (ValueError, OSError, TypeError) as error:
            record = {"status": "unavailable", "reference": label, "reason": str(error)}
        self.cache[key] = record
        record = {**record, "line": line}
        self.records.append(record)
        return record

    def link(self, record, alt="图片"):
        if record["status"] == "saved":
            return f"![{alt}](<{self.prefix}/{record['file']}>)"
        if record["status"] == "remote":
            url = quote(record["reference"], safe=":/?&=+#%@,;~")
            return f"![{alt}](<{url}>)"
        return "> 图片暂不可用。"

    def text(self, text, line):
        # Do not rewrite Markdown examples inside fenced or inline code.
        rendered, fence = [], None
        def replace(value):
            return MARKDOWN_IMAGE.sub(lambda match: self.link(self.capture(match[2].strip("<>"), line), match[1]), value)
        for row in text.splitlines(keepends=True):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", row)
            if fence:
                rendered.append(row)
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                    fence = None
            elif marker:
                fence = marker[1]
                rendered.append(row)
            else:
                start = 0
                for span in re.finditer(r"(`+).*?\1(?!`)", row):
                    rendered.append(replace(row[start:span.start()]))
                    rendered.append(span[0])
                    start = span.end()
                rendered.append(replace(row[start:]))
        return "".join(rendered)

    def block(self, block, line):
        mime = block.get("media_type", block.get("mimeType", block.get("mime_type")))
        reference = block.get("source", block.get("image_url", block.get("url", block.get("path"))))
        if isinstance(reference, dict) and reference.get("type") != "base64":
            reference = reference.get("url", reference.get("path", reference.get("data")))
        if reference is None and isinstance(block.get("data"), str):
            reference = {"type": "base64", "data": block["data"], "media_type": mime}
        return self.link(self.capture(reference, line, mime))

    def render(self, parts, line):
        rendered, text = [], []
        for part in parts:
            if part.get("type") == "text":
                text.append(part["text"])
            else:
                if text:
                    rendered.append(self.text("\n".join(text), line))
                    text = []
                rendered.append(self.block(part, line))
        if text:
            rendered.append(self.text("\n".join(text), line))
        return "\n\n".join(rendered)
