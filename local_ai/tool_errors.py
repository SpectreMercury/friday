"""Structured failures retain recovery information without exposing credentials."""
from dataclasses import dataclass, asdict

import aiohttp


@dataclass
class ToolFailure(Exception):
    code: str
    message: str
    retryable: bool = False
    requires_user: bool = False
    uncertain: bool = False

    def __str__(self):
        return self.message

    def payload(self):
        return asdict(self)


def classify_error(error):
    if isinstance(error, ToolFailure):
        return error
    if isinstance(error, PermissionError):
        return ToolFailure('access_denied', '权限不允许此操作；不能通过其他工具绕过。')
    if isinstance(error, FileNotFoundError):
        return ToolFailure('not_found', '指定文件或资源不存在；先核对路径或查找资源。')
    if isinstance(error, (TimeoutError, aiohttp.ServerTimeoutError)):
        return ToolFailure('timeout', '请求超时。读取可以退避重试；写入须先核对是否生效。', retryable=True)
    if isinstance(error, aiohttp.ClientResponseError):
        code = error.status
        return ToolFailure(f'http_{code}', f'服务返回 HTTP {code}。', retryable=code == 429 or code >= 500,
                           requires_user=code in (401, 403))
    if isinstance(error, aiohttp.ClientError):
        return ToolFailure('network_error', '网络连接失败。可退避重试或选择另一可用来源。', retryable=True)
    if isinstance(error, UnicodeError):
        return ToolFailure('unsupported_encoding', '内容编码不受支持；需要相应解析器。')
    if isinstance(error, ValueError):
        # Locally generated validation errors are useful; remote response bodies are not copied here.
        return ToolFailure('invalid_arguments', str(error)[:500])
    if isinstance(error, OSError):
        return ToolFailure('io_error', f'本地输入输出失败（errno={error.errno}）。')
    return ToolFailure('internal_error', f'工具内部错误（{type(error).__name__}）。')
