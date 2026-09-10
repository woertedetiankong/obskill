# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML==6.0.2"]
# ///
"""Isolated retrieval behavior tests; fake embeddings test mechanics, not model quality."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import yaml
from note_corpus import scan, glob_matches
from note_index import build_index, cache_path, read_cache, Ollama
from retrieval import retrieve, run_evaluation


class Fake:
    def __init__(self):
        self.calls = []
        self.version = 'v1'
        self.callback = None
        self.offline = False
        self.dimension = 3

    def profile(self, refresh=False):
        if self.offline:
            raise ValueError('offline')
        return {'provider': 'fake', 'model': 'fixture', 'digest': self.version}

    def embed(self, texts, query=False):
        self.calls.append((query, texts))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.offline:
            raise ValueError('offline')
        # Controlled fixture vectors, deliberately independent of lexical words.
        return [[1, 0, 0][:self.dimension] if ('sqlite' in text.lower() or 'writers waiting' in text.lower()) else
                [0, 1, 0][:self.dimension] for text in texts]


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.vault = self.base/'vault'
        self.vault.mkdir()
        self.cache = self.base/'cache'
        self.provider = Fake()

    def note(self, name, text, **meta):
        path = self.vault/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('---\n' + yaml.safe_dump(meta, allow_unicode=True) + '---\n' + text)
        return path

    def search(self, query, **kwargs):
        return retrieve(self.vault, query, provider=self.provider, cache_root=self.cache, **kwargs)

    def index(self, **kwargs):
        return build_index(self.vault, self.provider, self.cache, **kwargs)

    def test_chinese_alias_body_source_and_exact_code_search(self):
        self.note('阅读.md', '# 正文\n设置 retry_timeout_ms 后调用。', title='连接配置', aliases=['网络超时'], sources=['https://example.test/source731'])
        for query in ('网络超时', 'retry_timeout_ms', 'source731'):
            result = self.search(query, mode='fts')
            self.assertEqual(result['results'][0]['path'], '阅读.md')
        self.assertEqual(self.provider.calls, [])

    def test_semantic_only_paraphrase_and_hybrid_provenance(self):
        self.note('sqlite.md', '# SQLite\nBusy timeout for competing writers.')
        self.note('other.md', '# Garden\nPlant flowers.')
        self.index()
        result = self.search('writers waiting', mode='semantic')
        self.assertEqual(result['results'][0]['path'], 'sqlite.md')
        self.assertEqual(result['results'][0]['matched_by'], ['semantic'])
        both = self.search('SQLite')
        self.assertEqual(set(both['results'][0]['matched_by']), {'fts', 'semantic'})

    def test_incremental_updates_and_deletion_preserve_note_bytes(self):
        path = self.note('sqlite.md', '# SQLite\nFirst body.')
        original = path.read_bytes()
        first = self.index()
        second = self.index()
        self.assertGreater(first['embedded_chunks'], 0)
        self.assertEqual(second['embedded_chunks'], 0)
        self.assertEqual(path.read_bytes(), original)
        path.write_bytes(original + b'\nChanged body')
        stale = self.search('SQLite')
        self.assertTrue(any(d['code'] == 'partial_index' for d in stale['diagnostics']))
        self.assertEqual(stale['results'][0]['matched_by'], ['fts'])
        self.assertEqual(self.search('SQLite', mode='semantic')['results'], [])
        self.assertGreater(self.index()['embedded_chunks'], 0)
        path.unlink()
        self.assertEqual(self.search('SQLite')['results'], [])
        self.assertEqual(self.index()['removed_notes'], 1)

    def test_recall_filters_before_limiting_and_rejects_unknown_scope(self):
        for index in range(8):
            self.note(f'Lessons/wrong{index}.md', '# SQLite\nWrong project.', project='other', status='active')
        self.note('Lessons/right.md', '# SQLite\nRight project.', project='atlas', status='active', module='db', path_globs=['src/**/*.py'])
        self.note('Lessons/draft.md', '# SQLite\nDraft.', project='atlas', status='draft')
        self.note('Lessons/unknown.md', '# SQLite\nUnknown.')
        result = self.search('SQLite', mode='fts', recall=True, project='atlas', module='db', path='src/db.py', limit=1)
        self.assertEqual([row['path'] for row in result['results']], ['Lessons/right.md'])
        self.assertEqual(self.search('SQLite', mode='fts', recall=True, project='atlas', path='src/db.ts')['results'], [])
        with self.assertRaises(ValueError):
            self.search('SQLite', recall=True)

    def test_global_lessons_and_history_search(self):
        self.note('Lessons/global.md', '# SQLite\nGlobal.', project='global', status='active')
        self.note('Lessons/old.md', '# SQLite\nOld.', project='atlas', status='archived')
        self.assertEqual(len(self.search('SQLite', mode='fts')['results']), 2)
        self.assertEqual(len(self.search('SQLite', mode='fts', recall=True, project='atlas')['results']), 1)

    def test_replacement_chain_cycles_and_cross_project_target(self):
        self.note('Lessons/old.md', '# Old obscure731\nLegacy.', project='atlas', status='superseded', superseded_by='[[middle]]')
        self.note('Lessons/middle.md', '# Middle\nLegacy.', project='atlas', status='superseded', superseded_by='[[new]]')
        self.note('Lessons/new.md', '# New\nCurrent action.', project='atlas', status='active')
        result = self.search('obscure731', mode='fts', recall=True, project='atlas')
        self.assertEqual(result['results'][0]['path'], 'Lessons/new.md')
        self.assertEqual(result['results'][0]['replacement_chains'], [['Lessons/old.md', 'Lessons/middle.md']])
        self.note('Lessons/new.md', '# New', project='other', status='active')
        self.assertEqual(self.search('obscure731', mode='fts', recall=True, project='atlas')['results'], [])
        self.note('Lessons/new.md', '# New', project='atlas', status='superseded', superseded_by='[[old]]')
        result = self.search('obscure731', mode='fts', recall=True, project='atlas')
        self.assertEqual(result['results'], [])
        self.assertIn('replacement_cycle', [row['code'] for row in result['diagnostics']])

    def test_provider_failure_model_switch_and_dimension_mismatch(self):
        self.note('sqlite.md', '# SQLite\nLocal data.')
        self.index()
        self.provider.offline = True
        result = self.search('SQLite')
        self.assertEqual(result['results'][0]['matched_by'], ['fts'])
        self.assertIn('semantic_unavailable', [d['code'] for d in result['diagnostics']])
        self.provider.offline = False
        self.provider.version = 'v2'
        self.assertEqual(self.search('SQLite', mode='semantic')['results'], [])
        self.provider.version = 'v1'
        self.provider.dimension = 2
        result = self.search('SQLite')
        self.assertEqual(result['results'][0]['matched_by'], ['fts'])
        self.assertIn('semantic_unavailable', [d['code'] for d in result['diagnostics']])

    def test_source_edit_during_query_is_never_returned(self):
        note = self.note('sqlite.md', '# SQLite\nOriginal.')
        self.index()
        self.provider.callback = lambda: note.write_text('# Changed\nNew content')
        result = self.search('SQLite')
        self.assertEqual(result['results'], [])
        self.assertIn('source_changed', [d['code'] for d in result['diagnostics']])

    def test_index_edit_during_embedding_preserves_previous_cache(self):
        note = self.note('sqlite.md', '# SQLite\nOriginal.')
        self.index()
        location = cache_path(self.vault, self.cache)
        old_bytes = location.read_bytes()
        note.write_text('# SQLite\nNew pending content')
        self.provider.callback = lambda: note.write_text('# SQLite\nEdited while embedding')
        with self.assertRaisesRegex(ValueError, 'Vault changed'):
            self.index()
        self.assertEqual(location.read_bytes(), old_bytes)

    def test_exclusions_malformed_yaml_and_size_are_diagnosed(self):
        self.note('ok.md', '# Safe')
        self.note('Attachments/readme.md', '# Excluded attachment')
        self.note('.obsidian/hidden.md', '# Excluded setting')
        (self.vault/'bad.md').write_text('---\nfoo: [broken\n---\nbody')
        (self.vault/'large.md').write_text('x'*100)
        outside = self.base/'outside.md'
        outside.write_text('SECRET')
        (self.vault/'linked.md').symlink_to(outside)
        notes, diagnostics = scan(self.vault, max_bytes=70)
        self.assertEqual([row.path for row in notes], ['ok.md'])
        self.assertEqual({d['path'] for d in diagnostics}, {'bad.md', 'large.md'})

    def test_chunks_cover_tail_and_provide_original_line_locations(self):
        path = self.note('long.md', '# Intro\n' + 'Ordinary line.\n'*350 + '\n## Fix\nneedle731 exact_setting = 4000\n')
        result = self.search('needle731', mode='fts')
        row = result['results'][0]
        snippet = row['snippets'][0]
        lines = path.read_text().splitlines(keepends=True)
        self.assertIn('needle731', snippet['text'])
        self.assertEqual(''.join(lines[snippet['start_line']-1:snippet['end_line']]), snippet['text'])
        self.assertEqual(len(result['results']), 1)

    def test_cache_corruption_is_fallback_and_rebuild_recovers(self):
        self.note('sqlite.md', '# SQLite')
        self.index()
        location = cache_path(self.vault, self.cache)
        location.write_bytes(b'broken cache')
        self.assertEqual(self.search('SQLite')['results'][0]['matched_by'], ['fts'])
        self.index(rebuild=True)
        self.assertEqual(self.search('SQLite', mode='semantic')['results'][0]['path'], 'sqlite.md')

    def test_rename_reuses_vectors_and_does_not_duplicate_results(self):
        note = self.note('sqlite.md', '# SQLite', title='SQLite')
        self.index()
        # Filename is in the passage, so a new descriptive filename must reindex.
        note.rename(self.vault/'database.md')
        self.index()
        result = self.search('SQLite')
        self.assertEqual([row['path'] for row in result['results']], ['database.md'])

    def test_endpoint_and_cache_boundary(self):
        for url in ['https://example.com', 'http://127.0.0.1@evil.test', 'http://localhost/remote', 'file:///tmp/x']:
            with self.assertRaises(ValueError):
                Ollama(url)
        with self.assertRaises(ValueError):
            cache_path(self.vault, self.vault/'cache')
        self.assertTrue(glob_matches('src/**/*.py', 'src/db.py'))
        self.assertFalse(glob_matches('src/*.py', 'src/nested/db.py'))

    def test_evaluation_detects_forbidden_results(self):
        self.note('sqlite.md', '# SQLite')
        fixture = self.base/'cases.json'
        fixture.write_text(json.dumps({'cases': [{'query': 'SQLite', 'expected_paths': ['sqlite.md'], 'forbidden_paths': ['sqlite.md']}]}))
        report = run_evaluation(self.vault, fixture, self.provider, self.cache, mode='fts')
        self.assertEqual(report['passed'], 0)
        self.assertEqual(report['cases'][0]['forbidden_hits'], ['sqlite.md'])


if __name__ == '__main__':
    unittest.main()
