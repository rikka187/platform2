#!/usr/bin/env python3
"""WSGI 入口：给 gunicorn / uwsgi / 宝塔面板“Python 项目”使用。

用法（与 server.py 共用同一套配置与路由）：

.. code-block:: bash

    pip install gunicorn
    gunicorn -w 2 -k gthread --threads 4 -b 127.0.0.1:8787 wsgi:application

也可以在宝塔面板“网站 → Python 项目”里把启动方式选为
“gunicorn”，启动文件填 ``wsgi.py``，应用对象填 ``application``。

注意：本模块同时负责静态文件，因此直接用 gunicorn 暴露端口也能访问
完整页面；用 Nginx 反向代理时静态文件同样由这里返回，配置更简单。
"""
from __future__ import annotations

import sys
from http import HTTPStatus
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VENDOR = ROOT / '.vendor'
if VENDOR.exists() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from app import api, auth  # noqa: E402
from app.config import get_settings  # noqa: E402

# 与 server.py 保持一致：WSGI 路径同样要在启动时建库 + 导入案例2 种子数据 + 确保默认管理员存在。
# 否则经 gunicorn / 宝塔「Python 项目」启动时数据库不存在，登录接口会直接 500。
try:
    _boot = auth.bootstrap(get_settings())
    if _boot.get('created_admin'):
        print('[提示] 已创建默认管理员 admin / admin123，首次登录后必须修改口令。', flush=True)
except Exception as _exc:                                     # noqa: BLE001
    print(f'[警告] 启动初始化失败：{type(_exc).__name__}: {_exc}', flush=True)

SECURITY_HEADERS = (
    ('X-Content-Type-Options', 'nosniff'),
    ('X-Frame-Options', 'SAMEORIGIN'),
    ('Referrer-Policy', 'same-origin'),
)


def _reason(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:  # pragma: no cover
        return 'OK'


def _respond(start_response, status, headers, body=b'', head_only=False):
    headers = list(headers) + list(SECURITY_HEADERS) + [('Content-Length', str(len(body)))]
    start_response(f'{status} {_reason(status)}', headers)
    if head_only:
        return [b'']
    return [body]


def _request_path(environ) -> str:
    """PEP 3333 下 PATH_INFO 是 latin-1 解码的，需要还原成 UTF-8。"""
    raw = environ.get('PATH_INFO') or '/'
    try:
        return raw.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def application(environ, start_response):
    settings = get_settings()
    method = (environ.get('REQUEST_METHOD') or 'GET').upper()
    path = _request_path(environ)
    query = environ.get('QUERY_STRING') or ''
    target = path + (f'?{query}' if query else '')
    head_only = method == 'HEAD'

    if path.startswith('/api/'):
        body = b''
        if method in ('POST', 'PATCH', 'PUT'):
            raw_length = environ.get('CONTENT_LENGTH') or '0'
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                length = 0
            if length > settings.max_body_bytes:
                return _respond(
                    start_response, 413,
                    [('Content-Type', api.JSON_CONTENT_TYPE), ('Cache-Control', api.NO_STORE)],
                    api.json_bytes({'error': f'请求体过大（上限 {settings.max_body_bytes} 字节）。'}),
                )
            stream = environ.get('wsgi.input')
            body = stream.read(length) if stream and length > 0 else b''
        headers = {
            'Cookie': environ.get('HTTP_COOKIE', ''),
            'User-Agent': environ.get('HTTP_USER_AGENT', ''),
            'X-Real-IP': environ.get('HTTP_X_REAL_IP', ''),
            'X-Forwarded-For': environ.get('HTTP_X_FORWARDED_FOR', ''),
            'Host': environ.get('HTTP_HOST', ''),
        }
        response = api.handle_api(method, target, body, settings, headers)
        extra = [('Content-Type', api.JSON_CONTENT_TYPE), ('Cache-Control', api.NO_STORE)]
        extra += list((response.headers or {}).items())
        return _respond(
            start_response, response.status, extra,
            api.json_bytes(response.payload or {}), head_only,
        )

    if method not in {'GET', 'HEAD'}:
        return _respond(
            start_response, 405,
            [('Content-Type', api.JSON_CONTENT_TYPE), ('Cache-Control', api.NO_STORE)],
            api.json_bytes({'error': f'不支持的请求方法：{method}'}),
        )

    target_file = api.resolve_static(target, settings)
    if target_file is None:
        body = (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<title>404 页面不存在</title>'
            '<body style="font-family:system-ui,sans-serif;padding:40px">'
            '<h1>404</h1><p>请求的资源不存在。</p>'
            '<p><a href="/">返回平台首页</a></p></body></html>'
        ).encode('utf-8')
        return _respond(
            start_response, 404,
            [('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', api.NO_STORE)],
            body, head_only,
        )

    try:
        stat = target_file.stat()
    except OSError:
        return _respond(start_response, 404, [('Content-Type', 'text/plain; charset=utf-8')], b'not found')

    etag = '"%d-%d"' % (int(stat.st_mtime), stat.st_size)
    headers = [
        ('Content-Type', api.content_type_for(target_file)),
        ('Cache-Control', api.cache_control_for(target_file)),
        ('ETag', etag),
    ]
    if environ.get('HTTP_IF_NONE_MATCH') == etag:
        return _respond(start_response, 304, headers, b'', head_only)

    with target_file.open('rb') as handle:
        body = handle.read()
    return _respond(start_response, 200, headers, body, head_only)


# gunicorn 也可以写成 wsgi:app
app = application


__all__ = ['application', 'app']
