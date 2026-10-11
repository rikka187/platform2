#!/usr/bin/env python3
"""银犁物流仓配一体化协同平台 —— HTTP 服务入口。

本项目是纯标准库实现，不依赖 Flask/Django：

* 开发与 Windows 演示：``python server.py --host 0.0.0.0 --port 8787``
* 生产（Ubuntu + 宝塔面板）：由 systemd 托管本文件，Nginx 反向代理到
  ``127.0.0.1:8787``，见 ``deploy/install.sh`` 与
  ``docs/部署到Ubuntu宝塔面板.md``。
* 也可以用 WSGI 方式部署（gunicorn/uwsgi/宝塔 Python 项目）：``wsgi.py``。

相比原型版本，本文件补齐了生产必需的能力：请求上限、错误兜底、
安全响应头、ETag/304、无目录列表、访问日志、启动自检与优雅退出。
"""
from __future__ import annotations

import argparse
import errno
import os
import sys
import traceback
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 兼容旧版“把依赖放在 .vendor 目录”的用法
VENDOR = ROOT / '.vendor'
if VENDOR.exists() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from app import api, auth  # noqa: E402
from app.config import Settings, VERSION, get_settings  # noqa: E402


def _force_utf8_stdio() -> None:
    """保证中文日志在任何 locale（例如 LANG=C）下都能打印而不报错。

    systemd 服务的环境变量很干净，可能没有 UTF-8 locale；此时
    Python 的 stdout 会用 ASCII 编码，打印中文会抛 UnicodeEncodeError
    并导致服务启动失败。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except (ValueError, OSError):  # pragma: no cover
                pass


_force_utf8_stdio()

SERVER_NAME = 'YinliPlatform'
SECURITY_HEADERS = (
    ('X-Content-Type-Options', 'nosniff'),
    ('X-Frame-Options', 'SAMEORIGIN'),
    ('Referrer-Policy', 'same-origin'),
    ('Cache-Control', api.NO_STORE),
)


def format_http_date(timestamp: float) -> str:
    return formatdate(timestamp, usegmt=True)


class PlatformHandler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    timeout = 65

    settings: Settings = get_settings()

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:
        if not self.settings.access_log:
            return
        sys.stderr.write('[%s] %s - %s\n' % (self.log_date_time_string(), self.address_string(), fmt % args))
        sys.stderr.flush()

    def log_error(self, fmt: str, *args) -> None:
        sys.stderr.write('[%s] ERROR %s - %s\n' % (self.log_date_time_string(), self.address_string(), fmt % args))
        sys.stderr.flush()

    def version_string(self) -> str:
        """不对外暴露 Python 版本号。"""
        return self.server_version

    def _send_json(self, status: int, payload: dict, head_only: bool = False,
                   extra_headers: dict = None) -> None:
        raw = api.json_bytes(payload)
        self.send_response(status)
        self.send_header('Content-Type', api.JSON_CONTENT_TYPE)
        self.send_header('Content-Length', str(len(raw)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self._send_security_headers()
        self.end_headers()
        if not head_only:
            self.wfile.write(raw)

    def _request_headers(self) -> dict:
        """把请求头整理成字典，供接口层做会话与来源判断。"""
        wanted = ('Cookie', 'User-Agent', 'X-Real-IP', 'X-Forwarded-For', 'Host')
        return {name: self.headers.get(name, '') for name in wanted if self.headers.get(name)}

    def _send_security_headers(self) -> None:
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)

    def _read_body(self):
        """读取请求体。

        返回 ``(body, error_status, error_message)``：正常时后两项为 None。
        """
        raw_length = self.headers.get('Content-Length')
        if raw_length is None:
            return b'', None, None
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            return None, 400, 'Content-Length 不合法。'
        if length < 0:
            return None, 400, 'Content-Length 不合法。'
        if length > self.settings.max_body_bytes:
            # 有界地读掉超限请求体：直接关闭会在部分客户端上触发 RST，
            # 导致对方读不到 413 响应；超过 1MB 则不再继续读。
            to_drain = min(length, 1024 * 1024)
            while to_drain > 0:
                chunk = self.rfile.read(min(to_drain, 65536))
                if not chunk:
                    break
                to_drain -= len(chunk)
            self.close_connection = True
            return None, 413, f'请求体过大（上限 {self.settings.max_body_bytes} 字节）。'
        remaining = length
        chunks = []
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks), None, None

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch('GET')

    def do_HEAD(self) -> None:
        self._dispatch('HEAD')

    def do_POST(self) -> None:
        self._dispatch('POST')

    def do_PATCH(self) -> None:
        self._dispatch('PATCH')

    def do_PUT(self) -> None:
        self._dispatch('PUT')

    def do_DELETE(self) -> None:
        self._dispatch('DELETE')

    def do_OPTIONS(self) -> None:
        # 同源部署不需要 CORS，这里只回一个明确的 405。
        self._send_json(405, {'error': '不支持的请求方法：OPTIONS'})

    def handle(self) -> None:
        """吞掉客户端提前断开导致的异常，避免日志里出现无意义堆栈。"""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True

    def _dispatch(self, method: str) -> None:
        try:
            path = self.path.split('?', 1)[0]
            if path.startswith('/api/'):
                if method in ('POST', 'PATCH', 'PUT'):
                    body, error_status, error_message = self._read_body()
                    if error_status:
                        # 超限时请求体还没读完，必须关闭连接，否则残留字节
                        # 会被当成下一个请求解析（表现为莫名的 400/414）。
                        self.close_connection = True
                        self._send_json(error_status, {'error': error_message})
                        return
                else:
                    body = b''
                response = api.handle_api(method, self.path, body, self.settings, self._request_headers())
                self._send_json(response.status, response.payload or {},
                                head_only=(method == 'HEAD'), extra_headers=response.headers)
                return
            if method == 'POST':
                self._send_json(404, {'error': '接口不存在。'})
                return
            self._serve_static(head_only=(method == 'HEAD'))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 浏览器提前断开（例如用户切页），不需要报错
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            try:
                self._send_json(500, {'error': '服务器内部错误。'})
            except Exception:
                self.close_connection = True

    def _serve_static(self, head_only: bool = False) -> None:
        target = api.resolve_static(self.path, self.settings)
        if target is None:
            self._serve_not_found()
            return

        try:
            stat = target.stat()
        except OSError:
            self._serve_not_found()
            return

        etag = '"%d-%d"' % (int(stat.st_mtime), stat.st_size)
        last_modified = format_http_date(stat.st_mtime)
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self._send_security_headers()
            self.end_headers()
            return
        if_modified = self.headers.get('If-Modified-Since')
        if if_modified and if_modified == last_modified:
            self.send_response(304)
            self.send_header('ETag', etag)
            self._send_security_headers()
            self.end_headers()
            return

        self.send_response(200)
        self.send_header('Content-Type', api.content_type_for(target))
        self.send_header('Content-Length', str(stat.st_size))
        self.send_header('Last-Modified', last_modified)
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', api.cache_control_for(target))
        for name, value in SECURITY_HEADERS:
            if name != 'Cache-Control':
                self.send_header(name, value)
        self.end_headers()
        if head_only:
            return

        remaining = stat.st_size
        with target.open('rb') as handle:
            while remaining > 0:
                chunk = handle.read(min(self.settings.static_chunk_bytes, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.wfile.write(chunk)

    def _serve_not_found(self) -> None:
        page = (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<title>404 页面不存在</title>'
            '<body style="font-family:system-ui,sans-serif;padding:40px">'
            '<h1>404</h1><p>请求的资源不存在。</p>'
            '<p><a href="/">返回平台首页</a> · <a href="/api/health">服务健康检查</a></p>'
            '</body></html>'
        ).encode('utf-8')
        self.send_response(404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(page)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(page)


class PlatformHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def handle_error(self, request, client_address) -> None:
        """连接层异常只记一行，其余交给默认处理器打印堆栈。"""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            if PlatformHandler.settings.access_log:
                sys.stderr.write(f'[连接中断] {client_address}：{exc}\n')
                sys.stderr.flush()
            return
        super().handle_error(request, client_address)


def parse_args(argv=None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description='银犁物流仓配一体化协同平台服务')
    parser.add_argument('--host', default=settings.host, help=f'监听地址，默认 {settings.host}')
    parser.add_argument('--port', type=int, default=settings.port, help=f'监听端口，默认 {settings.port}')
    parser.add_argument('--data-dir', default=None, help='自定义 CSV 数据目录（默认 data/demo）')
    parser.add_argument('--no-access-log', action='store_true', help='关闭逐条访问日志')
    parser.add_argument('--check-only', action='store_true', help='只做启动自检并退出')
    parser.add_argument('--version', action='version', version=f'platform {VERSION}')
    return parser.parse_args(argv)


def build_settings_from_args(args) -> Settings:
    overrides = {}
    if args.host:
        overrides['PLATFORM_HOST'] = args.host
    if args.port:
        overrides['PLATFORM_PORT'] = str(args.port)
    if args.data_dir:
        overrides['PLATFORM_DATA_DIR'] = args.data_dir
    if args.no_access_log:
        overrides['PLATFORM_ACCESS_LOG'] = '0'
    base = get_settings()
    if not overrides:
        return base
    from app.config import build_settings

    env = dict(os.environ)
    env.update(overrides)
    return build_settings(env)


def print_check(settings: Settings) -> bool:
    ok, lines = api.startup_check(settings)
    print('=' * 62)
    print(f' 银犁物流仓配一体化协同平台  {VERSION}  启动自检')
    print('=' * 62)
    for level, message in lines:
        print(f'[{level}] {message}')
    print('-' * 62)
    print('自检结果：' + ('通过' if ok else '存在问题，请先修复上面的错误'))
    return ok


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = build_settings_from_args(args)

    api.clear_caches()

    # 建库建表 + 首次导入案例2 种子 + 创建默认管理员
    try:
        boot = auth.bootstrap(settings)
        if boot.get('admin'):
            print('=' * 62)
            print(' 首次初始化：已创建数据库与默认管理员账号')
            print(f"   用户名：{boot['admin']['username']}    初始口令：admin123")
            print('   首次登录后系统会要求立即修改口令。')
            print('=' * 62)
    except Exception as exc:  # pragma: no cover - 首次部署异常
        print(f'数据库初始化失败：{exc}', file=sys.stderr)
        traceback.print_exc()
        return 2

    ok = print_check(settings)
    if args.check_only:
        return 0 if ok else 2
    if not ok:
        print('服务未启动：请修复自检报错后重试。', file=sys.stderr)
        return 2

    PlatformHandler.settings = settings
    try:
        httpd = PlatformHTTPServer((settings.host, settings.port), PlatformHandler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            print(f'无法监听 {settings.host}:{settings.port}：{exc}', file=sys.stderr)
            print('端口被占用或权限不足。可换端口：PLATFORM_PORT=8788 或 --port 8788', file=sys.stderr)
        else:
            print(f'启动失败：{exc}', file=sys.stderr)
        return 3

    shown = '127.0.0.1' if settings.host in ('0.0.0.0', '::') else settings.host
    print(f'平台已启动：http://{shown}:{settings.port}  （监听 {settings.host}:{settings.port}）')
    print(f'健康检查：http://{shown}:{settings.port}/api/health')
    if settings.host in ('0.0.0.0', '::'):
        print('提示：局域网/公网访问请把 127.0.0.1 换成服务器 IP 或域名；生产环境建议只监听 127.0.0.1 并由 Nginx 反向代理。')
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print('\n收到退出信号，正在关闭...')
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
