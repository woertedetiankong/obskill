"""Pinned, downloadable CPU embeddings. No server or remote Python code."""
import hashlib
from http.client import HTTPException
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.request import Request, urlopen
from note_corpus import FORMAT_VERSION

MODEL = 'jinaai/jina-embeddings-v2-base-zh'
REVISION = 'c1ff9086a89a1123d7b5eff58055a665db4fb4b9'
FILES = {
    'tokenizer.json': (2030772, '0046da43cc8c424b317f56b092b0512aaaa65c4f925d2f16af9d9eeb4d0ef902'),
    'onnx/model_quantized.onnx': (161565239, '0a221ee9e6a6647ccc59cee7bdd26a7b8cf0c0cd3481a65f358d9585a23f02f4'),
}


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def model_directory(root=None):
    root = Path(root).expanduser() if root else Path.home()/'.cache/obsidian-experience/models'
    return root.resolve()/'jina-v2-base-zh'/REVISION


def valid_file(path, size, checksum):
    return path.is_file() and not path.is_symlink() and path.stat().st_size == size and sha256(path) == checksum


def model_status(root=None):
    directory = model_directory(root)
    files = [{'path': name, 'bytes': size, 'sha256': checksum,
              'valid': valid_file(directory/name, size, checksum)} for name, (size, checksum) in FILES.items()]
    return {'backend': 'local', 'model': MODEL, 'revision': REVISION, 'directory': str(directory),
            'download_bytes': sum(size for size, _ in FILES.values()), 'license': 'Apache-2.0',
            'source': f'https://huggingface.co/{MODEL}/tree/{REVISION}',
            'ready': all(row['valid'] for row in files), 'files': files,
            'runtime': 'CPU; onnxruntime + tokenizers + numpy; no Ollama',
            'network': 'Only download-model uses network; inference stays on this computer'}


def download_model(root=None, source_dir=None, progress=None):
    """Explicit download/import; verified files survive failed or interrupted attempts."""
    directory = model_directory(root)
    for name, (size, checksum) in FILES.items():
        target = directory/name
        if valid_file(target, size, checksum):
            if progress:
                progress(f'Already verified: {name}')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.download-', delete=False) as output:
                    temporary = Path(output.name)
                    if source_dir:
                        source = Path(source_dir).expanduser()/name
                        if not valid_file(source, size, checksum):
                            raise ValueError(f'Offline source failed verification: {name}')
                        with source.open('rb') as stream:
                            shutil.copyfileobj(stream, output)
                    else:
                        url = f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}'
                        if progress:
                            progress(f'Downloading {name}: {size/1e6:.1f} MB → {target} (attempt {attempt+1}/3)')
                        request = Request(url, headers={'User-Agent': 'obsidian-experience-local-retrieval/1'})
                        with urlopen(request, timeout=60) as response:
                            if not response.geturl().startswith('https://'):
                                raise ValueError('Model download requires HTTPS')
                            received, reported = 0, 0
                            while block := response.read(1024*1024):
                                received += len(block)
                                if received > size:
                                    raise ValueError(f'Model download exceeds expected size: {name}')
                                output.write(block)
                                if progress and (received-reported >= 16*1024*1024 or received == size):
                                    progress(f'{name}: {received/1e6:.1f}/{size/1e6:.1f} MB')
                                    reported = received
                if not valid_file(temporary, size, checksum):
                    raise ValueError(f'Model checksum or size mismatch: {name}')
                os.replace(temporary, target)
                break
            except (OSError, ValueError, HTTPException) as error:
                if source_dir or attempt == 2:
                    raise ValueError(f'Model installation failed for {name} ({type(error).__name__}); rerun download-model to retry') from error
                if progress:
                    progress(f'Download failed ({type(error).__name__}); retrying')
                time.sleep(attempt+1)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
    return model_status(root)


class LocalModel:
    default_min_score = 0.30

    def __init__(self, root=None):
        self.root = root
        self.directory = model_directory(root)
        self._signature = None
        self._session = None
        self._tokenizer = None

    def profile(self, refresh=False):
        try:
            signature = tuple((p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                              for p in (self.directory/name for name in FILES))
        except OSError as error:
            raise ValueError('Local model missing; run retrieval.py download-model (about 164 MB), then index') from error
        if signature != self._signature:
            if not model_status(self.root)['ready']:
                raise ValueError('Local model verification failed; run download-model to repair')
            self._signature = signature
            self._session = self._tokenizer = None
        return {'provider': 'local_onnx', 'model': MODEL, 'revision': REVISION,
                'model_digest': FILES['onnx/model_quantized.onnx'][1],
                'tokenizer_digest': FILES['tokenizer.json'][1], 'format': FORMAT_VERSION,
                'pooling': 'attention-mask-mean-l2-v1', 'query_instruction': '', 'output_dimension': 768}

    def embed(self, texts, query=False):
        self.profile()
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise ValueError('Local runtime missing; run this command with uv run scripts/retrieval.py') from error
        try:
            if self._session is None:
                options = ort.SessionOptions()
                options.intra_op_num_threads = min(4, os.cpu_count() or 1)
                options.inter_op_num_threads = 1
                self._session = ort.InferenceSession(str(self.directory/'onnx/model_quantized.onnx'),
                                                     sess_options=options, providers=['CPUExecutionProvider'])
                self._tokenizer = Tokenizer.from_file(str(self.directory/'tokenizer.json'))
                self._tokenizer.no_truncation()
                self._tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
            vectors = []
            # Small batches cap the working set for long bilingual passages.
            for start in range(0, len(texts), 2):
                encoded = self._tokenizer.encode_batch(texts[start:start+2])
                if any(len(item.ids) > 8192 for item in encoded):
                    raise ValueError('Local model input exceeds 8192 tokens; shorten the query or passage')
                inputs = {'input_ids': np.asarray([item.ids for item in encoded], dtype=np.int64),
                          'attention_mask': np.asarray([item.attention_mask for item in encoded], dtype=np.int64),
                          'token_type_ids': np.asarray([item.type_ids for item in encoded], dtype=np.int64)}
                feeds = {item.name: inputs[item.name] for item in self._session.get_inputs()}
                hidden = self._session.run(['last_hidden_state'], feeds)[0]
                mask = inputs['attention_mask'][..., None].astype(np.float32)
                pooled = (hidden*mask).sum(axis=1)/mask.sum(axis=1)
                norms = np.linalg.norm(pooled, axis=1, keepdims=True)
                if not np.isfinite(pooled).all() or np.any(norms == 0):
                    raise ValueError('Local model returned invalid vectors')
                vectors.extend((pooled/norms).tolist())
            return vectors
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f'Local embedding failed ({type(error).__name__}); verify model files and runtime') from error


def add_provider_arguments(parser):
    parser.add_argument('--backend', choices=['local', 'ollama'], default='local')
    parser.add_argument('--model-dir', type=Path, help='Model cache root, shared by all vaults')
    parser.add_argument('--model', help='Ollama model name; local backend uses the pinned bilingual model')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434', help='Optional Ollama loopback origin')


def make_provider(args):
    if args.backend == 'local':
        if args.model or args.endpoint != 'http://127.0.0.1:11434':
            raise ValueError('--model/--endpoint require --backend ollama')
        return LocalModel(args.model_dir)
    from note_index import Ollama
    return Ollama(args.endpoint, args.model or 'qwen3-embedding:0.6b')
