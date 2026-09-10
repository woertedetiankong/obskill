# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["PyYAML==6.0.2", "onnxruntime==1.23.2", "tokenizers==0.22.1", "numpy==2.3.4"]
# ///
"""Run real-model synthetic retrieval checks in a temporary vault."""
import argparse
import json
from pathlib import Path
import tempfile
import yaml
from local_model import LocalModel, add_provider_arguments, make_provider, model_status, download_model
from note_index import build_index
from retrieval import run_evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_provider_arguments(parser)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parent.parent/'assets/retrieval-fixture.json'
    data = json.loads(fixture.read_text(encoding='utf-8'))
    provider = make_provider(args)
    with tempfile.TemporaryDirectory(prefix='obsidian-retrieval-eval-') as temporary:
        base = Path(temporary)
        vault = base/'vault'
        vault.mkdir()
        for record in data['notes']:
            path = (vault/record['path']).resolve()
            if vault.resolve() not in path.parents:
                raise ValueError('Fixture path escapes temporary vault')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('---\n' + yaml.safe_dump(record['metadata'], allow_unicode=True) + '---\n\n' + record['body'], encoding='utf-8')
        index = build_index(vault, provider, base/'cache')
        reports = [run_evaluation(vault, fixture, provider, base/'cache', mode=mode)
                   for mode in ('fts', 'semantic', 'hybrid')]
        output = {'profile': index['profile'], 'synthetic': True, 'reports': reports}
        rendered = json.dumps(output, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + '\n', encoding='utf-8')
        print(rendered)
        # FTS weaknesses are visible baselines, not failures of semantic readiness.
        return 0 if all(report['passed'] == report['total'] for report in reports if report['mode'] != 'fts') else 2


if __name__ == '__main__':
    raise SystemExit(main())
