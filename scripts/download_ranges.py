"""Parallel HTTP ranges, preserving existing contiguous partial downloads."""
import concurrent.futures
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

from download_models import ROOT, acquire_download_lock, download, manifest

CHUNK = 64 * 1024 * 1024


def fetch(url, start, end, path, total):
    expected = end - start + 1
    if path.exists() and path.stat().st_size == expected:
        return
    temp = path.with_suffix('.tmp')
    for attempt in range(8):
        try:
            current = temp.stat().st_size if temp.exists() else 0
            if current > expected:
                temp.unlink()
                current = 0
            while current < expected:
                # Short requests avoid slow long-lived CDN connections and limit retry work.
                request_start = start + current
                request_end = min(end, request_start + 8 * 1024 * 1024 - 1)
                req = urllib.request.Request(url, headers={'Range': f'bytes={request_start}-{request_end}'})
                with urllib.request.urlopen(req, timeout=90) as r:
                    if r.status != 206 or r.headers.get('Content-Range') != f'bytes {request_start}-{request_end}/{total}':
                        raise RuntimeError('Server did not honor the requested range')
                    with temp.open('ab') as f:
                        while b := r.read(1024 * 1024):
                            f.write(b)
                current = temp.stat().st_size
                if current != request_end - start + 1:
                    raise RuntimeError('Incomplete response')
            if temp.stat().st_size != expected:
                raise RuntimeError('Incomplete range')
            temp.replace(path)
            return
        except Exception as exc:
            print(f'RETRY {path.name} attempt={attempt+1} {type(exc).__name__}: {exc}', flush=True)
            if attempt == 7:
                raise
            time.sleep(min(30, 2 ** attempt))


def run(entry):
    target = ROOT / entry['path']
    if target.exists():
        download(entry)
        chunks = target.with_suffix(target.suffix+'.chunks')
        if chunks.exists():
            shutil.rmtree(chunks)
        return
    partial = target.with_suffix(target.suffix+'.part')
    chunks = target.with_suffix(target.suffix+'.chunks')
    chunks.mkdir(parents=True, exist_ok=True)
    checkpoint = chunks / 'plan.json'
    if checkpoint.exists():
        prefix = json.loads(checkpoint.read_text())['prefix']
    else:
        prefix = partial.stat().st_size if partial.exists() else 0
        checkpoint.write_text(json.dumps({'prefix':prefix,'total':entry['size']}))
    # Appending is restartable: truncate back to the saved contiguous prefix.
    with partial.open('ab') as f:
        f.truncate(prefix)
    url = f'https://huggingface.co/{entry["repo"]}/resolve/{entry["revision"]}/{entry["filename"]}'
    ranges = [(start, min(entry['size']-1, start+CHUNK-1), chunks / f'{start}.chunk') for start in range(prefix, entry['size'], CHUNK)]
    print('RANGES '+entry['key']+f' from {prefix}',flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get('DOWNLOAD_RANGE_WORKERS', '6'))) as pool:
        futures = [pool.submit(fetch,url,start,end,path,entry['size']) for start,end,path in ranges]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    with partial.open('ab') as out:
        for _,_,path in ranges:
            with path.open('rb') as src:
                shutil.copyfileobj(src,out,8*1024*1024)
    if partial.stat().st_size != entry['size']:
        raise RuntimeError('Assembled size mismatch')
    partial.replace(target)
    download(entry)
    shutil.rmtree(chunks)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+', metavar='KEY', help='只下载 models.lock.json 中这些 key，例如 chat')
    args = parser.parse_args()
    lock = acquire_download_lock()
    order = ['image','image_vae','audio_vae','image_encoder','chat','video_lora','video_vae','video','video_encoder']
    entries = sorted(manifest(),key=lambda e:order.index(e['key']))
    if args.only:
        unknown = set(args.only) - {e['key'] for e in entries}
        if unknown:
            raise SystemExit('Unknown model keys: ' + ', '.join(sorted(unknown)))
        entries = [e for e in entries if e['key'] in args.only]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run,entries))
