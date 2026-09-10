"""Current Markdown corpus, source locations, metadata and lexical retrieval."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import unicodedata
import yaml

FORMAT_VERSION = 1
MAX_CHARS = 1600
EXCLUDED = {'.obsidian', '.git', '.trash', 'node_modules', 'Attachments', 'attachments'}


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def strings(value):
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def scalar(value):
    values = strings(value)
    return values[0] if len(values) == 1 else ''


@dataclass
class Chunk:
    text: str
    start: int
    end: int
    heading: str
    passage: str
    key: str


@dataclass
class Note:
    path: str
    absolute: Path
    fingerprint: str
    title: str
    meta: dict
    chunks: list

    @property
    def kind(self):
        explicit = scalar(self.meta.get('type'))
        return explicit or ('lesson' if self.path.split('/')[0].lower() in {'lessons', '经验'} else '')


def chunks_for(body, first_line, header):
    # Split at headings and size bounds. Overlap stays within the same section.
    pieces = []
    for number, line in enumerate(body.splitlines(keepends=True), first_line):
        for offset in range(0, len(line), MAX_CHARS):
            pieces.append((number, line[offset:offset + MAX_CHARS]))
    result, pending, size, heading = [], [], 0, ''
    fenced = False
    fence = ''

    def emit():
        if not pending:
            return
        text = ''.join(item[1] for item in pending)
        if not text.strip():
            return
        passage = header + '\nSection: ' + heading + '\n\n' + text
        result.append(Chunk(text, pending[0][0], pending[-1][0], passage=passage,
                            heading=heading, key=digest(str(FORMAT_VERSION) + '\n' + passage)))

    for number, line in pieces:
        bare = re.sub(r'^(?:> ?)+', '', line).strip()
        marker = re.match(r'^(`{3,}|~{3,})', bare)
        new_heading = re.match(r'^#{1,6}\s+(.+)', bare) if not fenced else None
        if new_heading:
            emit()
            pending, size = [], 0
            heading = new_heading[1]
        elif pending and size + len(line) > MAX_CHARS:
            emit()
            tail = pending[-1:] if len(pending[-1][1]) < 200 else []
            pending = tail
            size = sum(len(item[1]) for item in pending)
        pending.append((number, line))
        size += len(line)
        if marker:
            if not fenced:
                fenced, fence = True, marker[1][0]
            elif marker[1][0] == fence:
                fenced = False
    emit()
    if not result:
        passage = header + '\nSection: \n'
        result.append(Chunk('', first_line, first_line, '', passage, digest(str(FORMAT_VERSION) + '\n' + passage)))
    return result


def parse_note(path, vault, raw):
    text = raw.decode('utf-8-sig')
    lines = text.splitlines(keepends=True)
    meta, offset = {}, 0
    if lines and lines[0].strip() == '---':
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in {'---', '...'}), None)
        if end is None:
            raise ValueError('Unclosed YAML properties')
        meta = yaml.safe_load(''.join(lines[1:end])) or {}
        if not isinstance(meta, dict):
            raise ValueError('Properties must be a YAML mapping')
        offset = end + 1
    body = ''.join(lines[offset:])
    heading = re.search(r'^#\s+(.+)$', body, re.M)
    title = scalar(meta.get('title')) or (heading[1].strip() if heading else path.stem)
    relative = path.relative_to(vault).as_posix()
    header_fields = {
        'Title': title, 'Filename': path.stem, 'Aliases': ' '.join(strings(meta.get('aliases'))),
        'Description': scalar(meta.get('description')) or scalar(meta.get('summary')),
        'Tags': ' '.join(strings(meta.get('tags'))), 'Project': scalar(meta.get('project')),
        'Module': scalar(meta.get('module')), 'Sources': ' '.join(strings(meta.get('sources') or meta.get('source'))),
    }
    # Bound metadata so a malformed long description cannot exceed model context.
    header = '\n'.join(f'{key}: {value[:1600]}' for key, value in header_fields.items() if value)
    return Note(relative, path, digest(raw), title, meta, chunks_for(body, offset + 1, header))


def scan(vault, max_bytes=2 * 1024 * 1024, include_attachments=False):
    vault = Path(vault).expanduser().resolve()
    if not vault.is_dir():
        raise ValueError('Vault must be an existing directory')
    notes, diagnostics = [], []
    for directory, folders, files in os.walk(vault, followlinks=False):
        folders[:] = sorted(name for name in folders if not name.startswith('.') and not name.endswith('.assets')
                            and (name not in EXCLUDED or (include_attachments and name.lower() == 'attachments'))
                            and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() != '.md' or name.startswith('.') or path.is_symlink():
                continue
            try:
                if path.stat().st_size > max_bytes:
                    raise ValueError(f'Note exceeds {max_bytes} bytes; increase --max-note-mb to include it')
                with path.open('rb') as handle:
                    raw = handle.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise ValueError('Note grew beyond size limit during reading')
                notes.append(parse_note(path, vault, raw))
            except (OSError, ValueError, yaml.YAMLError) as error:
                diagnostics.append({'code': 'note_skipped', 'path': path.relative_to(vault).as_posix(),
                                    'message': str(error)[:300]})
    return notes, diagnostics


STOP = set('the a an is are to of and or for in on with how what should do i we it this that'.split())
SYNONYMS = {'数据库': ['database', 'sqlite', 'sql'], '并发': ['concurrency', 'concurrent'],
            '锁': ['lock', 'locking'], '缓存': ['cache'], '认证': ['authentication', 'auth'],
            '重试': ['retry'], '超时': ['timeout'], '循环': ['loop', 'looped'], '插件': ['plugin', 'plugins']}


def tokens(value, expand=False):
    normalized = unicodedata.normalize('NFKC', value).lower()
    output = [x for x in re.findall(r'[a-z0-9_]+(?:[.$/-][a-z0-9_]+)*', normalized) if x not in STOP]
    output += [part for token in list(output) for part in re.split(r'[.$/-]', token)
               if part != token and part and part not in STOP]
    for run in re.findall(r'[\u3400-\u9fff\U00020000-\U0002fa1f]+', normalized):
        output += [run] if len(run) == 1 else [run[i:i+2] for i in range(len(run)-1)]
    if expand:
        for source, alternatives in SYNONYMS.items():
            if source in normalized:
                output += alternatives
    return output


def lexical_scores(notes, query):
    query_tokens = set(tokens(query, expand=True))
    rows = []
    for note in notes:
        extra = ' '.join([note.title, note.absolute.stem, *strings(note.meta.get('aliases')),
                          *strings(note.meta.get('tags'))])
        for chunk in note.chunks:
            counts = Counter(tokens(chunk.passage + '\n' + extra + '\n' + extra))
            rows.append((note, chunk, counts, sum(counts.values())))
    if not rows or not query_tokens:
        return {}
    df = Counter(word for _, _, counts, _ in rows for word in query_tokens if word in counts)
    avg = sum(row[3] for row in rows) / len(rows) or 1
    scores = {}
    for note, chunk, counts, length in rows:
        score = 0.0
        for word in query_tokens:
            tf = counts.get(word, 0)
            if tf:
                idf = math.log(1 + (len(rows) - df[word] + 0.5) / (df[word] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
        if score > 0:
            scores[(note.path, chunk.key)] = score
    return scores


def glob_matches(pattern, path):
    pattern, path = pattern.replace('\\', '/'), path.replace('\\', '/').removeprefix('./')
    out, i = '', 0
    while i < len(pattern):
        if pattern[i:i+3] == '**/':
            out += '(?:.*/)?'; i += 3
        elif pattern[i:i+2] == '**':
            out += '.*'; i += 2
        elif pattern[i] == '*':
            out += '[^/]*'; i += 1
        elif pattern[i] == '?':
            out += '[^/]'; i += 1
        else:
            out += re.escape(pattern[i]); i += 1
    return re.fullmatch(out, path) is not None


def eligible(note, *, recall=False, project=None, status=None, kind=None, module=None, path=None, folder=None):
    if recall and (note.kind != 'lesson' or scalar(note.meta.get('status')) != 'active'):
        return False
    if project and scalar(note.meta.get('project')) not in {project, 'global'}:
        return False
    if status and status != 'all' and scalar(note.meta.get('status')) != status:
        return False
    if kind and note.kind != kind:
        return False
    if module and scalar(note.meta.get('module')) and scalar(note.meta.get('module')) != module:
        return False
    globs = strings(note.meta.get('path_globs'))
    if path and globs and not any(glob_matches(pattern, path) for pattern in globs):
        return False
    if folder and not (note.path == folder.rstrip('/') or note.path.startswith(folder.rstrip('/') + '/')):
        return False
    return True
