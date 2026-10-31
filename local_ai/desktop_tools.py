"""macOS desktop observations and explicit, bounded application launches."""
import json
import asyncio
import re
import sys
import tempfile
from pathlib import Path

from .process_tools import run_process
from .tool_errors import ToolFailure

DESKTOP_TOOLS = {'computer_permissions', 'computer_apps', 'computer_open_app', 'computer_screen'}


class DesktopTools:
    def __init__(self, confirm, guard, *, home=None):
        self.confirm, self.guard = confirm, guard
        self.home = Path(home or Path.home())
        self.binary = self.home / 'Applications/Friday Desktop.app/Contents/MacOS/FridayDesktop'

    def available(self):
        if sys.platform != 'darwin':
            return False, '桌面操作需要 macOS。'
        if not self.binary.is_file():
            return False, '需在 Mac 运行 .venv/bin/python scripts/install_desktop.py 安装 Friday Desktop。'
        return True, None

    async def invoke(self, *args):
        ready, reason = self.available()
        if not ready:
            raise ToolFailure('desktop_unavailable', reason, requires_user=True)
        self.guard()
        # LaunchServices gives the helper its own TCC identity. Executing its
        # binary as a Python child instead inherits the background host's identity.
        with tempfile.TemporaryDirectory(prefix='friday-desktop-') as folder:
            output = Path(folder) / 'result.json'
            errors = Path(folder) / 'errors.log'
            result = await run_process(['/usr/bin/open', '-W', '-n', '-g', str(self.binary.parents[2]),
                '--stdout', str(output), '--stderr', str(errors), '--args', *args], cwd=self.home, timeout=35)
            if result['exit_code']:
                raise ToolFailure('desktop_failed', '无法启动 Friday Desktop，请检查安装与 Mac 登录状态。')
            # open can return before a very short-lived app has fully exited.
            payload = None
            for _ in range(100):
                try:
                    payload = json.loads(output.read_text())
                    break
                except (OSError, ValueError):
                    await asyncio.sleep(.1)
            if not isinstance(payload, dict):
                raise ToolFailure('desktop_failed', '桌面工具未返回完整结果；请检查 Mac 是否解锁。')
        if payload.get('error'):
            raise ToolFailure(payload.get('error', 'desktop_failed'), payload.get('message', '桌面工具失败。'),
                              requires_user=payload.get('error') == 'screen_permission_required')
        return payload

    async def execute(self, name, args, *, screenshot=None):
        if name == 'computer_permissions':
            request = args.get('request', 'status')
            if request not in ('status', 'screen_recording', 'accessibility'):
                raise ValueError('未知系统权限。')
            if request != 'status':
                await self.confirm('在 Mac 上请求 Friday Desktop 的系统权限：' + request + '。需要本人在系统设置完成授权。')
            return await self.invoke('permissions', *([] if request == 'status' else [request]))
        if name == 'computer_apps':
            return {**await self.invoke('apps'), 'untrusted_content': True}
        if name == 'computer_open_app':
            bundle = args.get('bundle_id', '')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{1,199}', bundle):
                raise ValueError('请先查询应用列表，并使用有效 bundle_id。')
            await self.confirm(f'在 Mac 打开应用：{bundle}。可能切换当前前台应用。')
            return await self.invoke('open', bundle)
        if name == 'computer_screen':
            display = args.get('display', 1)
            if type(display) is not int or not 1 <= display <= 16 or screenshot is None:
                raise ValueError('显示器编号须为 1–16。')
            return await self.invoke('screen', str(screenshot), str(display))
        raise ValueError('未知桌面工具。')
