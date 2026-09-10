#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["PyYAML==6.0.2", "onnxruntime==1.23.2", "tokenizers==0.22.1", "numpy==2.3.4"]
# ///
"""Search current Obsidian Markdown with optional local semantic vectors."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import quote, unquote
from note_corpus import digest, eligible, lexical_scores, scalar, scan, strings
from local_model import LocalModel, add_provider_arguments, make_provider, model_status, download_model
from note_index import build_index, cache_path, manifest, read_cache, validate_vectors


def diagnostic(code, message, **fields):
    return {'code': code, 'message': message, **fields}


def coverage(notes, vectors, old_notes):
    keys = {chunk.key for note in notes for chunk in note.chunks}
    current = manifest(notes)
    return {'notes': len(notes), 'chunks': len(keys), 'fresh_chunks': len(keys & vectors.keys()),
            'missing_chunks': len(keys - vectors.keys()),
            'new_notes': len(set(current)-set(old_notes)),
            'changed_notes': sum(path in old_notes and value != old_notes[path] for path, value in current.items()),
            'deleted_notes': len(set(old_notes)-set(current))}


def inspect_cache(notes, provider, location):
    try:
        profile = provider.profile()
        vectors, old_notes, info = read_cache(location, profile)
        warnings = []
        if not info:
            warnings.append(diagnostic('index_missing', 'No index for the current model; run index'))
        return vectors, old_notes, info or profile, warnings
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError) as error:
        return {}, {}, {}, [diagnostic('semantic_unavailable', str(error)[:400])]


def note_rank(scores):
    best = {}
    for (path, key), score in scores.items():
        if path not in best or score > best[path][1]:
            best[path] = (key, score)
    return sorted(best.items(), key=lambda item: (-item[1][1], item[0]))


def resolve_replacement(note, notes):
    targets = strings(note.meta.get('superseded_by'))
    if len(targets) != 1:
        return None
    target = targets[0].strip()
    if target.startswith('[[') and target.endswith(']]'):
        target = target[2:-2].split('|')[0].split('#')[0]
    else:
        match = re.match(r'^\[[^]]*\]\((.+)\)$', target)
        target = match[1] if match else target
        target = unquote(target).split('#')[0]
    if not target or '://' in target or Path(target).is_absolute():
        return None
    options = {(Path(note.path).parent / target).as_posix(), target}
    options |= {item + '.md' for item in list(options) if not item.endswith('.md')}
    exact = [item for item in notes if item.path in options]
    if len(exact) == 1:
        return exact[0]
    loose = [item for item in notes if item.absolute.stem == target.removesuffix('.md') or item.title == target]
    return loose[0] if len(loose) == 1 else None


def follow_replacement(note, notes):
    chain, seen = [], set()
    while scalar(note.meta.get('status')) == 'superseded':
        if note.path in seen:
            return None, chain, 'replacement_cycle'
        seen.add(note.path)
        chain.append(note.path)
        note = resolve_replacement(note, notes)
        if note is None:
            return None, chain, 'replacement_missing'
    return note, chain, None


def retrieve(vault, query, *, mode='hybrid', provider=None, cache_root=None, limit=5, recall=False,
             project=None, status=None, kind=None, module=None, path=None, folder=None,
             min_score=None, max_bytes=2*1024*1024, include_attachments=False):
    if mode not in {'fts', 'semantic', 'hybrid'} or not query.strip() or limit < 1 or limit > 100:
        raise ValueError('Use a nonempty query, a supported mode and limit 1..100')
    if recall and (not project or status not in {None, 'active'}):
        raise ValueError('Recall requires --project and only recommends active lessons; use search for history')
    provider = provider or LocalModel()
    if min_score is None:
        min_score = getattr(provider, 'default_min_score', 0.45)
    if not math.isfinite(min_score) or not 0 <= min_score <= 1:
        raise ValueError('min-score must be between 0 and 1')
    notes, diagnostics = scan(vault, max_bytes, include_attachments)
    filters = dict(recall=recall, project=project, status=status, kind=kind, module=module, path=path, folder=folder)
    candidates = [note for note in notes if eligible(note, **filters)]
    # Replaced lessons may match the user's old terminology; their approved
    # replacement can be returned, with the chain visible, never the old action.
    historical = [note for note in notes if recall and scalar(note.meta.get('status')) == 'superseded'
                  and eligible(note, project=project, module=module, path=path, folder=folder)]
    pool = candidates + historical
    lexical = lexical_scores(pool, query) if mode != 'semantic' else {}
    semantic, vectors, info, old_notes = {}, {}, {}, {}
    location = cache_path(vault, cache_root, include_attachments)
    if mode != 'fts' and pool:
        vectors, old_notes, info, warnings = inspect_cache(notes, provider, location)
        diagnostics.extend(warnings)
        relevant_keys = {chunk.key for note in pool for chunk in note.chunks}
        missing = len(relevant_keys - vectors.keys())
        if missing:
            diagnostics.append(diagnostic('partial_index', f'{missing} relevant chunks lack fresh vectors; run index', missing_chunks=missing))
        if vectors and relevant_keys & vectors.keys():
            try:
                dimensions = len(next(iter(vectors.values())))
                query_vectors = validate_vectors(provider.embed([query], query=True), dimensions)
                if len(query_vectors) != 1:
                    raise ValueError('Expected one query vector')
                vector = query_vectors[0]
                for note in pool:
                    for chunk in note.chunks:
                        if chunk.key in vectors:
                            score = sum(a*b for a, b in zip(vector, vectors[chunk.key]))
                            if score >= min_score:
                                semantic[(note.path, chunk.key)] = score
                if provider.profile(refresh=True) != {key: value for key, value in info.items()
                                                     if key not in {'dimensions', 'indexed_at', 'notes', 'chunks'}}:
                    raise ValueError('Embedding model changed during search')
            except (ValueError, OSError, TypeError) as error:
                semantic = {}
                diagnostics.append(diagnostic('semantic_unavailable', str(error)[:400]))
        if not semantic:
            diagnostics.append(diagnostic('semantic_no_candidates', 'No fresh semantic result passed the threshold'))
    elif mode != 'fts' and not pool:
        diagnostics.append(diagnostic('no_applicable_notes', 'No notes match the requested scope/status; no embedding request sent'))

    scores, matched, best_keys = defaultdict(float), defaultdict(list), defaultdict(dict)
    for source, branch in [('fts', lexical), ('semantic', semantic)]:
        for rank, (note_path, (key, value)) in enumerate(note_rank(branch), 1):
            scores[note_path] += 1/(60+rank) if mode == 'hybrid' else value
            matched[note_path].append(source)
            best_keys[note_path][source] = (key, value)
    by_path = {note.path: note for note in notes}
    resolved, chains = {}, defaultdict(list)
    for source_path in sorted(scores, key=lambda item: (-scores[item], item)):
        source_note = by_path[source_path]
        note, chain, problem = follow_replacement(source_note, notes) if recall else (source_note, [], None)
        if problem:
            diagnostics.append(diagnostic(problem, 'Cannot resolve the replacement chain', path=source_path))
            continue
        if not note or not eligible(note, **filters):
            continue
        if chain:
            chains[note.path].append(chain)
        if note.path not in resolved or scores[source_path] > scores[resolved[note.path]]:
            resolved[note.path] = source_path
    rows = []
    for note_path, source_path in sorted(resolved.items(), key=lambda item: (-scores[item[1]], item[0])):
        note = by_path[note_path]
        # Recheck after potentially slow model execution; deleted/changed notes
        # and changed members of a replacement chain cannot become results.
        dependencies = {note_path, source_path, *(x for chain in chains[note_path] for x in chain)}
        try:
            current = all(not by_path[item].absolute.is_symlink() and
                          digest(by_path[item].absolute.read_bytes()) == by_path[item].fingerprint for item in dependencies)
        except OSError:
            current = False
        if not current:
            diagnostics.append(diagnostic('source_changed', 'Note changed during retrieval; retry', path=note_path))
            continue
        keys = {entry[0] for entry in best_keys[source_path].values()}
        snippets = [chunk for chunk in note.chunks if chunk.key in keys][:2]
        if not snippets:  # A replacement need not use the old search wording.
            snippets = note.chunks[:2]
        rows.append({
            'path': note.path, 'absolute_path': str(note.absolute), 'title': note.title, 'type': note.kind,
            'project': scalar(note.meta.get('project')), 'status': scalar(note.meta.get('status')),
            'description': scalar(note.meta.get('description')) or scalar(note.meta.get('summary')),
            'aliases': strings(note.meta.get('aliases')), 'tags': strings(note.meta.get('tags')),
            'sources': strings(note.meta.get('sources') or note.meta.get('source')),
            'applicability': {field: note.meta.get(field) for field in ('module', 'path_globs') if field in note.meta},
            'matched_by': matched[source_path], 'score': round(scores[source_path], 6),
            'score_kind': 'rrf_rank_fusion' if mode == 'hybrid' else ('cosine' if mode == 'semantic' else 'bm25'),
            'why_relevant': 'Matched ' + ', '.join(matched[source_path]) + (' through a superseded note' if chains[note_path] else ''),
            'replacement_chains': chains[note_path], 'source_sha256': note.fingerprint,
            'note_link': f'[{note.title}]({note.absolute.as_uri()})',
            'obsidian_uri': 'obsidian://open?path=' + quote(str(note.absolute), safe=''),
            'snippets': [{'heading': chunk.heading, 'start_line': chunk.start, 'end_line': chunk.end, 'text': chunk.text} for chunk in snippets],
        })
        if len(rows) >= limit:
            break
    return {'query': query, 'mode': mode, 'min_score': min_score, 'recall': recall, 'filters': filters, 'vault': str(Path(vault).resolve()),
            'cache': str(location), 'profile': info, 'coverage': coverage(notes, vectors, old_notes) if mode != 'fts' else {'notes': len(notes)},
            'results': rows, 'diagnostics': diagnostics,
            'limitations': ['Markdown only; no OCR/PDF/HTML content extraction', 'Scores indicate ranking, not correctness or confidence']}


def run_evaluation(vault, fixture, provider, cache_root=None, mode='hybrid', **scan_options):
    data = json.loads(Path(fixture).read_text(encoding='utf-8'))
    cases = data.get('cases', [])
    if not cases:
        raise ValueError('Evaluation fixture requires a nonempty cases list')
    results = []
    for case in cases:
        expected, forbidden = set(case.get('expected_paths', [])), set(case.get('forbidden_paths', []))
        if not expected and not case.get('expect_empty'):
            raise ValueError('Each case needs expected_paths or expect_empty')
        options = {key: case[key] for key in ('recall', 'project', 'status', 'kind', 'module', 'path', 'folder') if key in case}
        outcome = retrieve(vault, case['query'], provider=provider, cache_root=cache_root, mode=mode, limit=5, **options, **scan_options)
        paths = [row['path'] for row in outcome['results']]
        first = next((rank for rank, path in enumerate(paths, 1) if path in expected), None)
        leaked = sorted(set(paths) & forbidden)
        passed = (not paths if case.get('expect_empty') else bool(expected & set(paths[:3]))) and not leaked
        results.append({'query': case['query'], 'paths': paths, 'recall_at_3': len(expected & set(paths[:3]))/len(expected) if expected else None,
                        'reciprocal_rank': 1/first if first else 0, 'forbidden_hits': leaked, 'passed': passed,
                        'diagnostics': outcome['diagnostics'], 'min_score': outcome['min_score']})
    scored = [row for row in results if row['recall_at_3'] is not None]
    return {'mode': mode, 'cases': results, 'passed': sum(row['passed'] for row in results), 'total': len(results),
            'recall_at_3': sum(row['recall_at_3'] for row in scored)/len(scored) if scored else None,
            'mrr_at_5': sum(row['reciprocal_rank'] for row in scored)/len(scored) if scored else None,
            'fixture_sha256': digest(Path(fixture).read_bytes()), 'synthetic': data.get('synthetic', False),
            'claim_boundary': 'Small specified-corpus retrieval check; not a general accuracy or usefulness guarantee'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('model-status', 'download-model'):
        sub = commands.add_parser(name)
        sub.add_argument('--model-dir', type=Path)
        if name == 'download-model':
            sub.add_argument('--source-dir', type=Path, help='Import verified model files from an offline directory')
    for name in ('search', 'recall', 'index', 'status', 'evaluate'):
        sub = commands.add_parser(name)
        sub.add_argument('--vault', type=Path, required=True)
        sub.add_argument('--cache-root', type=Path)
        add_provider_arguments(sub)
        sub.add_argument('--max-note-mb', type=int, default=2)
        sub.add_argument('--include-attachments', action='store_true')
        if name in {'search', 'recall'}:
            sub.add_argument('query', nargs='?', help='Query text; omit or use - to read stdin')
            sub.add_argument('--mode', choices=['fts', 'semantic', 'hybrid'], default='hybrid')
            sub.add_argument('--limit', type=int, default=5)
            sub.add_argument('--project', required=name == 'recall')
            sub.add_argument('--status', choices=['active', 'draft', 'archived', 'superseded', 'all'])
            sub.add_argument('--type', dest='kind')
            sub.add_argument('--module')
            sub.add_argument('--path', help='Target source-code path for path_globs applicability')
            sub.add_argument('--folder', help='Vault-relative note folder')
            sub.add_argument('--min-score', type=float, help='Cosine cutoff; default local 0.30, Ollama 0.45')
        if name == 'index':
            sub.add_argument('--rebuild', action='store_true', help='Recreate this vault cache; notes remain untouched')
        if name == 'evaluate':
            sub.add_argument('--fixture', type=Path, required=True)
            sub.add_argument('--mode', choices=['fts', 'semantic', 'hybrid'], default='hybrid')
    args = parser.parse_args()
    if args.command in {'model-status', 'download-model'}:
        outcome = model_status(args.model_dir) if args.command == 'model-status' else download_model(
            args.model_dir, args.source_dir, progress=lambda message: print(message, file=sys.stderr))
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    if args.max_note_mb < 1:
        parser.error('--max-note-mb must be positive')
    provider = make_provider(args)
    options = dict(max_bytes=args.max_note_mb*1024*1024, include_attachments=args.include_attachments)
    if args.command == 'index':
        outcome = build_index(args.vault, provider, args.cache_root, args.rebuild,
                              progress=lambda done, total: print(f'Embedding {done}/{total}', file=sys.stderr), **options)
    elif args.command == 'status':
        notes, diagnostics = scan(args.vault, **options)
        location = cache_path(args.vault, args.cache_root, args.include_attachments)
        vectors, previous, info, warnings = inspect_cache(notes, provider, location)
        outcome = {'cache': str(location), 'profile': info, 'coverage': coverage(notes, vectors, previous),
                   'diagnostics': diagnostics + warnings}
    elif args.command == 'evaluate':
        outcome = run_evaluation(args.vault, args.fixture, provider, args.cache_root, args.mode, **options)
    else:
        query = args.query if args.query and args.query != '-' else sys.stdin.read(16001)
        if len(query) > 16000:
            raise ValueError('Query exceeds 16000 characters')
        outcome = retrieve(args.vault, query, provider=provider, cache_root=args.cache_root, mode=args.mode,
                           recall=args.command == 'recall', limit=args.limit,
                           **{key: getattr(args, key) for key in ('project', 'status', 'kind', 'module', 'path', 'folder', 'min_score')}, **options)
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    if args.command == 'evaluate' and outcome['passed'] != outcome['total']:
        return 2
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
