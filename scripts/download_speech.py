"""Download pinned speech models and verify all large-file SHA-256 hashes."""
import hashlib
import json
from pathlib import Path
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
for kind, item in json.loads((ROOT / 'speech-models.lock.json').read_text()).items():
    path = ROOT / 'models/speech' / kind
    snapshot_download(item['repo'], revision=item['revision'], local_dir=path, max_workers=4)
    for entry in item['files']:
        file = path / entry['path']
        if file.stat().st_size != entry['size']:
            raise RuntimeError(f'Wrong size: {file}')
        if entry['sha256']:
            h = hashlib.sha256()
            with file.open('rb') as source:
                for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
                    h.update(block)
            if h.hexdigest() != entry['sha256']:
                raise RuntimeError(f'Wrong checksum: {file}')
    (path / '.verified.json').write_text(json.dumps({'revision': item['revision']}))
    print(kind, 'verified', flush=True)
