"""HTTP 传输无关的小工具：响应结构、Cookie 解析与会话 Cookie 构造。

``server.py``（http.server）与 ``wsgi.py``（gunicorn）共用这一层，保证两种
部署方式下登录、会话、错误结构完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.auth import COOKIE_NAME, SESSION_HOURS


@dataclass
class Response:
    """一次 API 调用的完整结果。"""

    status: int = 200
    payload: Optional[dict] = None
    headers: Dict[str, str] = field(default_factory=dict)

    def with_header(self, name: str, value: str) -> 'Response':
        self.headers[name] = value
        return self


def parse_cookies(header: Optional[str]) -> Dict[str, str]:
    """把 'Cookie: a=1; b=2' 解析成字典（容忍空值、空格、重复键取最后一个）。"""
    jar: Dict[str, str] = {}
    if not header:
        return jar
    for chunk in str(header).split(';'):
        if '=' not in chunk:
            continue
        name, _, value = chunk.partition('=')
        name = name.strip()
        if name:
            jar[name] = value.strip()
    return jar


def session_token(cookies: Dict[str, str]) -> str:
    return cookies.get(COOKIE_NAME, '')


def session_cookie(token: str, hours: int = SESSION_HOURS, secure: bool = False) -> str:
    parts = [f'{COOKIE_NAME}={token}', 'Path=/', 'HttpOnly', 'SameSite=Lax',
             f'Max-Age={int(hours * 3600)}']
    if secure:
        parts.append('Secure')
    return '; '.join(parts)


def clear_session_cookie(secure: bool = False) -> str:
    parts = [f'{COOKIE_NAME}=', 'Path=/', 'HttpOnly', 'SameSite=Lax', 'Max-Age=0']
    if secure:
        parts.append('Secure')
    return '; '.join(parts)


def split_path(target: str) -> List[str]:
    """'/api/customers/12' -> ['api', 'customers', '12']"""
    path = str(target or '/').split('?', 1)[0]
    return [segment for segment in path.split('/') if segment]


__all__ = [
    'Response', 'clear_session_cookie', 'parse_cookies', 'session_cookie',
    'session_token', 'split_path',
]
