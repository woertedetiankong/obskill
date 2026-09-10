"""Optional loopback Ollama provider and a backend-independent disposable vector cache."""
from contextlib import closing
import ipaddress
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from note_corpus import FORMAT_VERSION, digest, scan

QUERY_INSTRUCTION = 'Instruct: Retrieve relevant notes or coding lessons that answer the query.\nQuery: '
SCHEMA = 1


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Embedding endpoint redirects are disabled')


class Ollama:
    def __init__(self, endpoint='http://127.0.0.1:11434', model='qwen3-embedding:0.6b', timeout=60):
        parsed = urlsplit(endpoint)
        try:
            local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            local = False
        if not local or parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise ValueError('Only a loopback Ollama origin is supported; remote note transmission is not enabled')
        self.endpoint, self.model, self.timeout = endpoint.rstrip('/'), model, timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self._profile = None

    def request(self, path, payload=None):
        request = Request(self.endpoint + path, data=None if payload is None else json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('Embedding response exceeds size limit')
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get('error'):
                raise ValueError('Embedding server returned an invalid response')
            return value
        except (URLError, OSError, json.JSONDecodeError) as error:
            # Do not echo response bodies which may include submitted passages.
            raise ValueError(f'Local embedding service unavailable ({type(error).__name__}); check Ollama and the installed model') from error

    def profile(self, refresh=False):
        if self._profile and not refresh:
            return self._profile
        rows = self.request('/api/tags').get('models', [])
        aliases = {self.model, self.model + ':latest'}
        match = next((row for row in rows if isinstance(row, dict) and (row.get('name') in aliases or row.get('model') in aliases)), None)
        if not match or not isinstance(match.get('digest'), str) or not match['digest']:
            raise ValueError(f'Model {self.model} is not installed; choose an installed embedding model')
        self._profile = {'provider': 'ollama', 'endpoint': self.endpoint, 'model': self.model,
                         'model_digest': match['digest'], 'format': FORMAT_VERSION,
                         'query_instruction': QUERY_INSTRUCTION if 'qwen3-embedding' in self.model.lower() else ''}
        return self._profile

    def embed(self, texts, query=False):
        profile = self.profile()
        inputs = [profile['query_instruction'] + text if query else text for text in texts]
        value = self.request('/api/embed', {'model': self.model, 'input': inputs, 'truncate': False, 'keep_alive': '5m'})
        vectors = value.get('embeddings')
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ValueError('Embedding response has the wrong number of vectors')
        return validate_vectors(vectors)


def validate_vectors(vectors, dimensions=None):
    normalized = []
    for row in vectors:
        if not isinstance(row, list) or not row or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in row):
            raise ValueError('Invalid embedding values')
        dimensions = dimensions or len(row)
        if len(row) != dimensions:
            raise ValueError('Embedding dimension mismatch')
        norm = math.sqrt(sum(x*x for x in row))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError('Invalid embedding norm')
        normalized.append([x / norm for x in row])
    return normalized


def profile_id(profile):
    return digest(json.dumps(profile, sort_keys=True))


def cache_path(vault, cache_root=None, include_attachments=False):
    vault = Path(vault).expanduser().resolve()
    root = Path(cache_root).expanduser().resolve() if cache_root else Path.home() / '.cache' / 'obsidian-experience'
    if root == vault or vault in root.parents:
        raise ValueError('The disposable cache must be outside the vault')
    return root / digest(str(vault) + str(include_attachments))[:24] / 'index.sqlite3'


def manifest(notes):
    return {note.path: note.fingerprint for note in notes}


def read_cache(path, profile):
    if not path.exists():
        return {}, {}, {}
    if path.is_symlink():
        raise ValueError('Cache cannot be a symlink')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA:
            raise ValueError('Cache schema mismatch; run index --rebuild')
        key = profile_id(profile)
        row = db.execute('SELECT info FROM profiles WHERE id=?', (key,)).fetchone()
        info = json.loads(row[0]) if row else {}
        values = db.execute('SELECT key, vector FROM vectors WHERE profile=?', (key,)).fetchall()
        vectors = {key: vector for (key, _), vector in zip(values, validate_vectors([json.loads(raw) for _, raw in values], info.get('dimensions')))}
        notes = dict(db.execute('SELECT path, hash FROM notes WHERE profile=?', (key,)).fetchall())
        return vectors, notes, info


def ensure_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, info TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS vectors(profile TEXT NOT NULL, key TEXT NOT NULL, vector TEXT NOT NULL, PRIMARY KEY(profile,key));
        CREATE TABLE IF NOT EXISTS notes(profile TEXT NOT NULL, path TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(profile,path));
    ''')
    db.execute(f'PRAGMA user_version={SCHEMA}')


def build_index(vault, provider, cache_root=None, rebuild=False, max_bytes=2*1024*1024, include_attachments=False, progress=None):
    notes, diagnostics = scan(vault, max_bytes, include_attachments)
    profile = provider.profile()
    location = cache_path(vault, cache_root, include_attachments)
    vectors, old_notes, info = ({}, {}, {}) if rebuild else read_cache(location, profile)
    passages = {chunk.key: chunk.passage for note in notes for chunk in note.chunks}
    wanted = [key for key in passages if key not in vectors]
    fresh = {key: vectors[key] for key in passages if key in vectors}
    dimensions = info.get('dimensions')
    for start in range(0, len(wanted), 12):
        keys = wanted[start:start+12]
        result = validate_vectors(provider.embed([passages[key] for key in keys]), dimensions)
        if len(result) != len(keys):
            raise ValueError('Embedding count mismatch')
        if result:
            dimensions = len(result[0])
        fresh.update(zip(keys, result))
        if progress:
            progress(min(start+len(keys), len(wanted)), len(wanted))
    if provider.profile(refresh=True) != profile:
        raise ValueError('Embedding model changed during indexing; retry')
    current, current_diagnostics = scan(vault, max_bytes, include_attachments)
    if manifest(current) != manifest(notes) or current_diagnostics != diagnostics:
        raise ValueError('Vault changed during indexing; previous cache preserved, retry')
    location.parent.mkdir(parents=True, exist_ok=True)
    if location.is_symlink():
        raise ValueError('Cache cannot be a symlink')
    # --rebuild creates a new disposable DB; no personal note is ever written.
    target = location.with_name(f'.rebuild-{os.getpid()}-{time.time_ns()}.sqlite3') if rebuild else location
    key = profile_id(profile)
    info = {**profile, 'dimensions': dimensions, 'indexed_at': time.time(), 'notes': len(notes), 'chunks': len(passages)}
    try:
        with closing(sqlite3.connect(target, timeout=10)) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in {0, SCHEMA}:
                raise ValueError('Cache schema mismatch; run index --rebuild')
            ensure_schema(db)
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM vectors WHERE profile=?', (key,))
                db.execute('DELETE FROM notes WHERE profile=?', (key,))
                db.executemany('INSERT INTO vectors VALUES(?,?,?)', [(key, item, json.dumps(vector)) for item, vector in fresh.items()])
                db.executemany('INSERT INTO notes VALUES(?,?,?)', [(key, path, value) for path, value in manifest(notes).items()])
                db.execute('INSERT OR REPLACE INTO profiles VALUES(?,?)', (key, json.dumps(info)))
        if rebuild:
            os.replace(target, location)
    finally:
        if rebuild:
            target.unlink(missing_ok=True)
    return {'cache': str(location), 'profile': info, 'embedded_chunks': len(wanted),
            'reused_chunks': len(passages)-len(wanted), 'removed_notes': len(set(old_notes)-set(manifest(notes))),
            'diagnostics': diagnostics}
