"""Identify the source snapshot loaded by a service process, without credentials."""
import hashlib
from pathlib import Path


def source_fingerprint():
    root = Path(__file__).resolve().parents[1]
    files = sorted((root / 'local_ai').glob('*.py'))
    files += sorted((root / 'workflows').glob('*.json'))
    files += [root / 'pi' / name for name in
              ('runner-v2.mjs', 'context.mjs', 'progress.mjs', 'tool-context.mjs', 'package-lock.json')]
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + b'\0')
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


BUILD_ID = source_fingerprint()

if __name__ == '__main__':
    print(BUILD_ID)
