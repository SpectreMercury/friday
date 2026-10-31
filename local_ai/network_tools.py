"""Credential-free network probes and reviewed repairs with verification."""
import asyncio
import json
import socket
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp

from .process_tools import run_process
from .tool_errors import ToolFailure

NETWORK_TOOLS = {'network_diagnose', 'network_proxy_options', 'network_proxy_test', 'network_repair'}
_repair_lock = asyncio.Lock()


def diagnostic_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value):
        raise ValueError('需要有效诊断地址。')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError('诊断地址无效。') from None
    local_http = parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if not parsed.hostname or (parsed.scheme != 'https' and not local_http) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('使用不含凭据、查询参数或片段的 HTTPS 地址；HTTP 仅允许本机回环地址。')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('无效端口。')
    return parsed


class NetworkTools:
    def __init__(self, confirm, guard, *, home=None):
        self.confirm, self.guard = confirm, guard
        self.home = Path(home or Path.home())
        # Explicit integration with the installed Clash Verge local controller.
        # Never inspect subscription files or extract proxy passwords.
        self.controller = Path('/tmp/verge/verge-mihomo.sock')

    async def api(self, path, *, method='GET', data=None, params=None):
        self.guard()
        if not self.controller.is_socket():
            raise ToolFailure('proxy_controller_unavailable', '未发现本机 Clash Verge 控制接口；请先启动应用。', requires_user=True)
        connector = aiohttp.UnixConnector(path=str(self.controller))
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=12)) as client:
            async with client.request(method, 'http://localhost' + path, json=data, params=params, allow_redirects=False) as response:
                if response.status not in (200, 204):
                    raise ToolFailure('proxy_api_failed', f'代理控制接口返回 HTTP {response.status}；没有读取或更改认证配置。')
                if response.status == 204:
                    return {}
                raw = await response.content.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ToolFailure('proxy_response_too_large', '代理列表过大。')
                return json.loads(raw)

    async def probe(self, url):
        parsed = diagnostic_url(url)
        self.guard()
        result = await run_process(['/usr/bin/curl', '--disable', '--silent', '--show-error', '--head',
            '--connect-timeout', '6', '--max-time', '12', '--output', '/dev/null',
            '--write-out', '%{json}', '--url', url], cwd=self.home, timeout=15)
        try:
            metrics = json.loads(result['stdout'])
        except ValueError:
            metrics = {}
        status = int(metrics.get('http_code', 0))
        transport_ok = result['exit_code'] == 0 and status > 0
        return {'url': url, 'transport_ok': transport_ok, 'http_status': status,
                'service_ok': transport_ok and status < 500,
                'certificate_verified': parsed.scheme == 'https' and transport_ok and metrics.get('ssl_verify_result') == 0,
                'remote_ip': metrics.get('remote_ip'), 'seconds': metrics.get('time_total'),
                'curl_exit': result['exit_code'], 'error': result['stderr'][:500],
                'note': 'HEAD 检查；401/403 表示连接已建立但需要认证，不能据此声称业务操作成功。未跟随重定向。'}

    async def proxy_test(self, node, url):
        diagnostic_url(url)
        if not isinstance(node, str) or not 1 <= len(node) <= 200:
            raise ValueError('需要有效节点名称。')
        result = await self.api('/proxies/' + quote(node, safe='') + '/delay', params={'url': url, 'timeout': '8000'})
        return {'node': node, 'url': url, 'delay_ms': result.get('delay'),
                'reachable': isinstance(result.get('delay'), (int, float)),
                'note': '仅该节点的控制器探测结果；不代表系统流量已切换，也不代替 curl 的证书验证。'}

    async def execute(self, name, args):
        if name == 'network_proxy_options':
            result = await self.api('/proxies')
            groups = [{'name': key, 'selected': row.get('now'), 'options': row.get('all', [])[:150]}
                      for key, row in result.get('proxies', {}).items() if row.get('type') == 'Selector']
            return {'groups': groups[:40], 'limit': 40, 'options_limit': 150, 'untrusted_content': True}
        if name == 'network_proxy_test':
            return await self.proxy_test(args['node'], args['url'])
        if name == 'network_diagnose':
            parsed = diagnostic_url(args['url'])
            async def dns():
                try:
                    rows = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM), 6)
                    return {'addresses': sorted({row[4][0] for row in rows})[:16]}
                except (OSError, TimeoutError):
                    return {'error': 'DNS 解析失败或超时。'}
            address, https, proxies = await asyncio.gather(dns(), self.probe(args['url']),
                run_process(['/usr/sbin/scutil', '--proxy'], cwd=self.home, timeout=6))
            return {'dns': address, 'http': https, 'system_proxy': proxies['stdout'][:4000],
                    'clash_controller_available': self.controller.is_socket(),
                    'note': '系统代理配置不涵盖全部 TUN 路由；198.18.x.x 可能是代理 Fake-IP，不能仅凭此判定 DNS 故障。'}
        if name != 'network_repair':
            raise ValueError('未知网络工具。')
        url = args['url']
        diagnostic_url(url)
        action = args['action']
        if action == 'flush_dns':
            await self.confirm(f'刷新 Mac 用户可用的 DNS 缓存，然后检查 {url}。不改 DNS 服务器、代理或证书验证。')
            self.guard()
            result = await run_process(['/usr/bin/dscacheutil', '-flushcache'], cwd=self.home, timeout=10)
            return {'action': action, 'command_succeeded': result['exit_code'] == 0,
                    'verification': await self.probe(url), 'error': result['stderr'][:500]}
        if action != 'select_proxy':
            raise ValueError('仅支持 flush_dns 或 select_proxy；隧道重启使用 mac_restart_service。')
        group, node = args.get('group'), args.get('node')
        if not all(isinstance(v, str) and 1 <= len(v) <= 200 for v in (group, node)):
            raise ValueError('切换代理需要已查询的 group 和 node。')
        endpoint = '/proxies/' + quote(group, safe='')
        before = await self.api(endpoint)
        if before.get('type') != 'Selector' or node not in before.get('all', []):
            raise ValueError('该组不可手动选择或不包含指定节点。')
        previous = before.get('now')
        if not previous:
            raise ValueError('未取得原节点，不能安全切换。')
        if previous == node:
            return {'changed': False, 'group': group, 'selected': node, 'verification': await self.probe(url)}
        candidate = await self.proxy_test(node, url)
        if not candidate['reachable']:
            raise ToolFailure('candidate_unreachable', '候选节点测试失败，未切换。')
        await self.confirm(f'切换 Mac 代理组「{group}」：{previous} → {node}。这会影响使用该组的其他应用。随后检查 {url}；失败则尝试恢复原节点。不会修改手机代理。')
        async with _repair_lock:
            current = await self.api(endpoint)
            if current.get('now') != previous or node not in current.get('all', []):
                raise ToolFailure('proxy_changed', '代理设置已被其他操作更改，请重新诊断。')
            self.guard()
            try:
                await self.api(endpoint, method='PUT', data={'name': node})
                verification = await self.probe(url)
                selected = (await self.api(endpoint)).get('now')
            except (Exception, asyncio.CancelledError) as error:
                # A PUT can take effect before its response is lost. Read back
                # before compensating; never overwrite an intervening user change.
                restored = False
                try:
                    selected = (await self.api(endpoint)).get('now')
                    if selected == node:
                        await self.api(endpoint, method='PUT', data={'name': previous})
                        restored = (await self.api(endpoint)).get('now') == previous
                    elif selected == previous:
                        restored = True
                except Exception:
                    pass
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise ToolFailure('proxy_repair_interrupted',
                    '代理切换或复测中断。' + ('已核实恢复原节点。' if restored else '未能核实恢复；请查询当前代理选择后处理。'),
                    uncertain=not restored) from None
            result = {'changed': True, 'group': group, 'previous': previous, 'selected': selected,
                      'verification': verification, 'verified': verification['service_ok'] and selected == node,
                      'persistence': '当前代理控制器的选择；是否跨订阅更新保留由代理应用决定。'}
            if not result['verified'] and selected == node:
                await self.api(endpoint, method='PUT', data={'name': previous})
                result['rolled_back'] = (await self.api(endpoint)).get('now') == previous
                result['selected'] = previous if result['rolled_back'] else None
            return result
