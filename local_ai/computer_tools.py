"""Administrator-only host tools. Shell is powerful and requires explicit approval."""
import asyncio
import os
from pathlib import Path
import tempfile

from .engine import ROOT
from .process_tools import run_process
from .desktop_tools import DESKTOP_TOOLS, DesktopTools
from .network_tools import NETWORK_TOOLS, NetworkTools

HOST_TOOLS = {'computer_list', 'computer_find', 'computer_read', 'computer_write', 'terminal'} | DESKTOP_TOOLS | NETWORK_TOOLS
WEB_TOOLS = {'web_search', 'web_read'}


class ComputerTools:
    def __init__(self, job, admin, approve=None, home=None, guard=None):
        self.job, self.admin, self.approve = job, admin, approve
        self.home = (home or Path.home()).resolve()
        self.guard = guard or (lambda: None)
        self.desktop = DesktopTools(self.confirm, self.guard, home=self.home)
        self.network = NetworkTools(self.confirm, self.guard, home=self.home)

    def path(self, value):
        if not isinstance(value, str) or len(value) > 1000:
            raise ValueError('请提供有效文件路径。')
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.home/path
        path = path.resolve()
        if not path.is_relative_to(self.home):
            raise ValueError('文件工具当前仅开放管理员的主目录。')
        relative = path.relative_to(self.home)
        if any(part.startswith('.') for part in relative.parts) or (relative.parts and relative.parts[0] == 'Library'):
            raise ValueError('文件工具不读取隐藏配置、凭据或 Library；请换用普通文档目录。')
        for protected in ('runtime', 'vendor', 'models'):
            if path.is_relative_to((ROOT/protected).resolve()):
                raise ValueError('不能通过文件工具访问助手的内部数据目录。')
        if path.suffix.lower() in ('.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3'):
            raise ValueError('该文件类型不通过对话文件工具读取。')
        return path

    async def confirm(self, text, scope=None):
        if not self.approve or not await self.approve(self.job, text, **({'scope':scope} if scope else {})):
            raise ValueError('操作未获确认或已取消，没有执行。')
        self.guard()

    async def execute(self, name, args):
        if not self.admin or self.job['user_id'] != self.admin:
            raise ValueError('电脑操作只向管理员开放。')
        self.guard()
        if name in DESKTOP_TOOLS:
            return await self.desktop.execute(name, args)
        if name in NETWORK_TOOLS:
            return await self.network.execute(name, args)
        if name == 'terminal':
            command = args.get('command')
            if not isinstance(command, str) or not 1 <= len(command) <= 2000:
                raise ValueError('命令长度须为 1–2000 字符。')
            cwd = self.path(args.get('cwd') or str(self.home))
            if not cwd.is_dir():
                raise ValueError('工作目录不存在。')
            # Exact read-only commands only. Shell syntax/arguments need review.
            safe = {'pwd', 'date', 'uptime', 'uname -a', 'sw_vers', 'df -h'}
            if command not in safe:
                await self.confirm(f'执行终端命令（以你的 Mac 账号运行）：\n目录：{cwd}\n\n{command}\n\n该命令可能读取或修改账号可访问的文件，也可能联网。')
            return await run_process(['/bin/zsh', '-f', '-c', command], cwd=cwd, timeout=60)
        path = self.path(args.get('path') or str(self.home))
        if name == 'computer_list':
            rows = []
            for item in sorted(path.iterdir(), key=lambda p:p.name):
                try:
                    self.path(str(item))
                except ValueError:
                    continue
                rows.append({'name': item.name, 'directory': item.is_dir()})
                if len(rows) >= 100:
                    break
            return {'path': str(path), 'entries': rows, 'limit': 100}
        if name == 'computer_find':
            query = args.get('query', '')
            if not isinstance(query, str) or not 1 <= len(query) <= 100:
                raise ValueError('请输入 1–100 字符的文件名关键词。')
            matches, examined = [], 0
            # No symlink recursion; bounded scan yields control for cancellation.
            for base, directories, files in os.walk(path, followlinks=False):
                examined += 1
                if examined >= 10000:
                    return {"paths": matches, "truncated": True}
                kept = []
                for directory in directories:
                    try:
                        self.path(str(Path(base)/directory))
                        kept.append(directory)
                    except ValueError:
                        pass
                directories[:] = kept
                for name_ in files:
                    examined += 1
                    if query.lower() in name_.lower():
                        try:
                            matches.append(str(self.path(str(Path(base)/name_))))
                        except ValueError:
                            pass
                    if len(matches) >= 30 or examined >= 10000:
                        return {'paths': matches, 'truncated': True}
                await asyncio.sleep(0)
            return {'paths': matches, 'truncated': False}
        if name == 'computer_read':
            if not path.is_file() or path.stat().st_size > 1_000_000:
                raise ValueError('请提供小于 1 MB 的普通文本文件。')
            if path.stat().st_nlink != 1:
                raise ValueError('不读取硬链接文件。')
            with path.open(encoding='utf-8') as source:
                text = source.read(8001)
            return {'path': str(path), 'text': text[:8000], 'truncated': len(text)>8000, 'untrusted_content': True}
        if name == 'computer_write':
            content = args.get('content')
            if not isinstance(content, str) or len(content) > 2000:
                raise ValueError('每次写入最多 2000 字符，以便完整确认。')
            if not path.parent.is_dir() or path.is_dir():
                raise ValueError('父目录必须存在，目标不能是目录。')
            await self.confirm(f"{'覆盖' if path.exists() else '创建'}文件：{path}\n\n完整内容：\n{content}", scope="computer_write:"+str(path.parent))
            if self.path(str(path)) != path:
                raise ValueError('目标路径已变化，请重新确认。')
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.assistant-')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as target:
                    target.write(content)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return {'saved': str(path), 'characters': len(content)}
        raise ValueError('未知电脑工具。')
