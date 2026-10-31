"""Compare chat backends (llama.cpp / mlx-lm / MTPLX) through one OpenAI-compatible API.

Start exactly one backend on --url first, with the bot service stopped. Measures:
  decode   generation speed on a ~400-token answer (tok/s after the first token)
  prefill  time to first token for a long document prompt (cold)
  reuse    time to first token for a follow-up that shares that long prefix
  tools    whether a tool call comes back as structured tool_calls
Results are printed and appended as one JSON line to --out.
"""
import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [{'type': 'function', 'function': {
    'name': 'calculate', 'description': 'Evaluate a decimal arithmetic expression exactly.',
    'parameters': {'type': 'object', 'properties': {'expression': {'type': 'string'}},
                   'required': ['expression']}}}]


def stream(client, url, model, messages, max_tokens, tools=None):
    body = {'model': model, 'messages': messages, 'max_tokens': max_tokens, 'stream': True,
            'temperature': 0.7, 'top_p': 0.8, 'seed': 7, 'stream_options': {'include_usage': True}}
    if tools:
        body['tools'] = tools
    start = time.perf_counter()
    first = None
    text, calls, usage, chunks = '', [], None, 0
    with client.stream('POST', url + '/v1/chat/completions', json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith('data: ') or line == 'data: [DONE]':
                continue
            event = json.loads(line[6:])
            usage = event.get('usage') or usage
            for choice in event.get('choices') or []:
                delta = choice.get('delta') or {}
                piece = (delta.get('content') or '') + (delta.get('reasoning_content') or delta.get('reasoning') or '')
                if piece or delta.get('tool_calls'):
                    first = first or time.perf_counter()
                    chunks += 1
                text += delta.get('content') or ''
                calls += delta.get('tool_calls') or []
    end = time.perf_counter()
    tokens = (usage or {}).get('completion_tokens') or chunks
    return {'ttft': (first or end) - start, 'decode_s': end - (first or end), 'tokens': tokens,
            'prompt_tokens': (usage or {}).get('prompt_tokens'), 'text': text, 'calls': calls}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8095')
    parser.add_argument('--model', default='local')
    parser.add_argument('--label', required=True)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--out', type=Path, default=ROOT / 'runtime/benchmarks/chat-backends.jsonl')
    args = parser.parse_args()
    client = httpx.Client(timeout=900)
    run = lambda *a, **k: stream(client, args.url, args.model, *a, **k)

    # Warm-up loads weights and compiles kernels; not measured.
    run([{'role': 'user', 'content': '你好'}], 16)

    decode = []
    for i in range(args.runs):
        r = run([{'role': 'user', 'content': f'第{i + 1}次：请写一篇约600字的中文短文，介绍深圳的四季、交通和美食。'}], 400)
        decode.append(r['tokens'] / max(r['decode_s'], 1e-6))

    document = (ROOT / 'docs/development-notes.md').read_text()[:14000]
    ask = [{'role': 'system', 'content': '你是一个严谨的技术助手。'},
           {'role': 'user', 'content': document + '\n\n用三句话概括上面文档的主要功能。'}]
    prefill, reuse = [], []
    for i in range(args.runs):
        # A unique first line forces a cold read of the whole document each run.
        cold = [{'role': 'system', 'content': f'你是一个严谨的技术助手。（会话 {time.time_ns()}）'}] + ask[1:]
        r = run(cold, 96)
        prefill.append({'ttft': r['ttft'], 'prompt_tokens': r['prompt_tokens']})
        follow = cold + [{'role': 'assistant', 'content': r['text'] or '好的。'},
                         {'role': 'user', 'content': '再用一句话说明它如何保护隐私。'}]
        reuse.append(run(follow, 64)['ttft'])

    tools_ok = 0
    for _ in range(args.runs):
        r = run([{'role': 'user', 'content': '用 calculate 工具计算 37.5*91-12.25，只调用工具。'}], 512, tools=TOOLS)
        names = [c.get('function', {}).get('name') for c in r['calls']]
        tools_ok += 'calculate' in names

    result = {
        'label': args.label, 'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'decode_tok_s': round(statistics.median(decode), 1),
        'prefill_ttft_s': round(statistics.median(p['ttft'] for p in prefill), 2),
        'prefill_prompt_tokens': prefill[0]['prompt_tokens'],
        'prefill_tok_s': round(prefill[0]['prompt_tokens'] / statistics.median(p['ttft'] for p in prefill), 1)
        if prefill[0]['prompt_tokens'] else None,
        'reuse_ttft_s': round(statistics.median(reuse), 2),
        'tool_calls_ok': f'{tools_ok}/{args.runs}',
        'decode_runs': [round(x, 1) for x in decode],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('a') as f:
        f.write(json.dumps(result, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
