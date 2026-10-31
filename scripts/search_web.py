"""Search-only child process. No local file extraction or arbitrary URL fetching."""
import json
import sys
from ddgs import DDGS

def search(query):
    attempts = []
    # Explicit ordered fallback: failure of one provider does not end retrieval.
    for backend in ('bing', 'duckduckgo', 'brave'):
        try:
            rows = DDGS(timeout=8).text(query, max_results=5, backend=backend, safesearch='moderate')
            if rows:
                return {'results': [{'title': r.get('title','')[:250], 'url': r.get('href','')[:1500],
                                     'snippet': r.get('body','')[:700]} for r in rows[:5]],
                        'provider': backend, 'attempts': attempts}
            attempts.append({'provider': backend, 'error': 'empty_results'})
        except Exception as exc:
            # Do not expose request headers, credentials or provider tracebacks.
            attempts.append({'provider': backend, 'error': type(exc).__name__})
    return {'results': [], 'attempts': attempts, 'unavailable': True}


if __name__ == '__main__':
    print(json.dumps(search(json.load(sys.stdin)['query']), ensure_ascii=False))
