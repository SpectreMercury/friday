"""Public web retrieval. Page text is untrusted data, never instructions."""
import asyncio
import ipaddress
import json
import socket
import sys
from urllib.parse import urlsplit, urljoin

import aiohttp
from bs4 import BeautifulSoup

from .engine import ROOT
from .process_tools import run_process
from .tool_errors import ToolFailure


def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('仅支持无凭据的公开 HTTP/HTTPS 网页。')
    if parsed.port not in (None, 80, 443):
        raise ValueError('仅允许公开网页的标准端口。')
    host = parsed.hostname.lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local')):
        raise ValueError('网页工具不能访问本机或内网。')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not address.is_global:
        raise ValueError('网页工具不能访问本机或内网。')
    return url


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM, family=family)
        # TUN proxies may expose synthetic benchmark-range DNS addresses.
        # Resolve only that case via a fixed public DoH endpoint; still reject
        # non-public answers and connect to the validated numeric address.
        if infos and all(ipaddress.ip_address(i[4][0]) in ipaddress.ip_network('198.18.0.0/15') for i in infos):
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=False) as session:
                async with session.get('https://dns.google/resolve', params={'name': host, 'type': 'A'}, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ValueError('公开 DNS 查询失败。')
                    result = await response.json()
            infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (item['data'], port))
                     for item in result.get('Answer', []) if item.get('type') == 1]
            if not infos:
                raise ValueError('没有找到公开网页地址。')
        rows = []
        for af, _, proto, _, addr in infos:
            if not ipaddress.ip_address(addr[0]).is_global:
                raise ValueError('网页地址解析到了非公开网络。')
            rows.append(dict(hostname=host, host=addr[0], port=port, family=af, proto=proto, flags=socket.AI_NUMERICHOST))
        return rows

    async def close(self):
        pass


async def read_web(url):
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=25),
                                    headers={'User-Agent': 'LocalAssistant/1.0'}, trust_env=False) as client:
        for _ in range(5):
            public_url(url)
            async with client.get(url, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('Location', ''))
                    continue
                if response.status >= 400:
                    raise ToolFailure(f'http_{response.status}', f'网页返回 HTTP {response.status}。',
                                      retryable=response.status == 429 or response.status >= 500,
                                      requires_user=response.status == 401)
                content_type = response.headers.get('Content-Type', '').lower()
                if not any(t in content_type for t in ('text/', 'application/json', 'application/xhtml')):
                    raise ValueError('仅支持文字或 HTML 网页；此链接是其他文件类型。')
                data = bytearray()
                async for block in response.content.iter_chunked(65536):
                    data.extend(block)
                    if len(data) > 2_000_000:
                        raise ValueError('网页内容过大，请换一个具体页面。')
                html = data.decode(response.charset or 'utf-8', errors='replace')
                soup = BeautifulSoup(html, 'html.parser')
                title = soup.title.get_text(' ', strip=True) if soup.title else ''
                for element in soup(['script', 'style', 'noscript', 'nav', 'footer', 'header']):
                    element.decompose()
                main = soup.find('main') or soup.find('article') or soup
                text = main.get_text(' ', strip=True)
                quality = 'empty' if not text else 'ok'
                if any(marker in title+text[:600] for marker in ('APP扫码，享七天免登录','扫描二维码登录','验证中心')) or urlsplit(url).hostname=='account.dianping.com':
                    quality = 'access_gate'
                if len(text) < 500 and any(marker in text.lower() for marker in ('验证码', '验证后', '登录后', 'captcha', 'access denied')):
                    quality = 'access_gate'
                links = []
                for anchor in main.find_all('a', href=True):
                    link = urljoin(url, anchor['href'])
                    if link.startswith(('http://', 'https://')):
                        links.append({'title': anchor.get_text(' ', strip=True)[:100], 'url': link[:1500]})
                    if len(links) >= 12:
                        break
                return {'url': url, 'title': title[:300], 'text': text[:8000], 'truncated': len(text) > 8000,
                        'links': links, 'untrusted_content': True, 'retrieval_status': quality,
                        'hint': '页面未提供可用正文，请换具体来源；不能把空页或验证页当作已核实资料。' if quality != 'ok' else ''}
        raise ValueError('网页重定向次数过多。')


async def search_web(query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ValueError('搜索词长度须为 1–300 字符。')
    result = await run_process([sys.executable, str(ROOT/'scripts/search_web.py')], cwd=ROOT,
                               stdin=json.dumps({'query': query}).encode(), timeout=40)
    if result['exit_code']:
        raise ToolFailure('search_process_failed', '搜索进程失败；可改用其他已配置能力。', retryable=True)
    try:
        payload = json.loads(result['stdout'])
    except ValueError:
        raise ToolFailure('invalid_provider_response', '搜索服务返回格式异常。') from None
    if payload.get('unavailable'):
        attempts = '、'.join(f"{item['provider']}({item['error']})" for item in payload['attempts'])
        raise ToolFailure('search_providers_unavailable', f'搜索来源未取得结果：{attempts}。可改用其他能力或已有来源。', retryable=True)
    return {**payload, 'untrusted_content': True}
