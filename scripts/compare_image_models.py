"""Same-seed A/B of image diffusion weights; nothing else in the graph changes.

Run with the resident service idle (the engine lock refuses a busy machine).
Usage: python -m scripts.compare_image_models qwen-image-2.1-UC-Q4_K_M.gguf qwen-image-2.1-UC-Q8_0.gguf
Each brief/seed pair is saved side by side, in argument order, with a JSON
report of render seconds and peak ComfyUI resident memory per model.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import psutil
from PIL import Image, ImageDraw

from local_ai.engine import COMFY, Engine, RUNTIME, build_workflow

BRIEFS = [
    'A ginger cat asleep on a teal velvet sofa beside a sunny window, a knitted cream blanket over the armrest, '
    'soft afternoon light from the left, shallow depth of field, warm natural colors.',
    'A storefront sign on a red brick wall reads “晨光咖啡” in bold white Chinese characters, '
    'with a small steaming coffee cup icon to the left, evening street light, photographed straight on.',
    'Close-up portrait of an elderly fisherman with a weathered face and grey beard, wearing a navy knit cap, '
    'overcast seaside light, fine skin texture and individual beard hairs visible.',
    'A flat minimalist app icon: one geometric white F centered on a deep blue rounded square, '
    'equal margins, no shadows, no gradients.',
]
SEEDS = [1234, 98765]


async def sample_peak(engine, peak, stop):
    while not stop.is_set():
        process = engine.process
        if process and process.returncode is None:
            try:
                parent = psutil.Process(process.pid)
                rss = sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)])
                peak[0] = max(peak[0], rss)
            except psutil.Error:
                pass
        await asyncio.sleep(0.5)


async def main(models):
    if len(models) < 2:
        raise SystemExit(__doc__)
    for name in models:
        if not (COMFY / 'models/diffusion_models' / name).is_file():
            raise SystemExit('Missing model file: ' + name)
    folder = RUNTIME / 'image-model-ab' / str(time.time_ns())
    folder.mkdir(parents=True)
    engine = Engine()
    report = {'models': models, 'seeds': SEEDS, 'renders': []}
    try:
        outputs = {}
        # Model-major order: each model loads once, then serves every brief/seed.
        for model in models:
            # Untimed warm-up so Comfy start and weight loading are not billed to one brief.
            warmup = build_workflow('image', BRIEFS[0], 'ab-warmup', smoke=True, seed=0)
            warmup['model']['inputs']['unet_name'] = model
            await engine.render('image', BRIEFS[0], 'ab-warmup', workflow=warmup)
            for b, brief in enumerate(BRIEFS):
                for seed in SEEDS:
                    graph = build_workflow('image', brief, f'ab-{b}-{seed}-{model}', seed=seed)
                    graph['model']['inputs']['unet_name'] = model
                    peak, stop = [0], asyncio.Event()
                    sampler = asyncio.create_task(sample_peak(engine, peak, stop))
                    started = time.monotonic()
                    path = await engine.render('image', brief, f'ab-{b}-{seed}', workflow=graph)
                    seconds = time.monotonic() - started
                    stop.set()
                    await sampler
                    outputs[model, b, seed] = path
                    report['renders'].append({'model': model, 'brief': b, 'seed': seed, 'seconds': round(seconds, 1),
                                              'peak_rss_gb': round(peak[0] / 2**30, 2), 'output': str(path)})
                    print(json.dumps(report['renders'][-1], ensure_ascii=False), flush=True)
        for b in range(len(BRIEFS)):
            for seed in SEEDS:
                tiles = [Image.open(outputs[model, b, seed]).convert('RGB') for model in models]
                sheet = Image.new('RGB', (sum(t.width for t in tiles), tiles[0].height + 28), 'white')
                x = 0
                for model, tile in zip(models, tiles):
                    sheet.paste(tile, (x, 28))
                    ImageDraw.Draw(sheet).text((x + 8, 8), model, fill='black')
                    x += tile.width
                sheet.save(folder / f'brief{b}-seed{seed}.png')
    finally:
        await engine.stop()
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('REPORT ' + str(folder))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
