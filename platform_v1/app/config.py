"""运行时配置。

所有可调项都通过环境变量提供，服务器（systemd / 宝塔面板）只需要维护
一个 ``platform.env`` 文件，不需要改代码。

环境变量一览见 ``deploy/platform.env.example`` 与
``docs/部署到Ubuntu宝塔面板.md``。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

# platform_v1 目录（本文件的上一级）
APP_ROOT = Path(__file__).resolve().parents[1]

VERSION = 'v1.3-deploy'


@dataclass(frozen=True)
class Settings:
    """一次进程生命周期内生效的配置快照。"""

    root: Path = APP_ROOT
    host: str = '127.0.0.1'
    port: int = 8787
    data_dir: Path = field(default_factory=lambda: APP_ROOT / 'data' / 'demo')
    static_dir: Path = field(default_factory=lambda: APP_ROOT / 'app' / 'static')
    db_file: Path = field(default_factory=lambda: APP_ROOT / 'data' / 'platform.db')

    # 请求限制
    max_body_bytes: int = 65536
    static_chunk_bytes: int = 65536

    # 求解并发与缓存
    max_concurrent_solves: int = 2
    solve_queue_timeout: float = 120.0
    solve_cache_ttl: float = 300.0
    solve_cache_size: int = 64
    # 页面默认口径：默认沿用文档/汇报使用的启发式口径，保证五情景对比与
    # KPI 卡片自洽；答辩现场可在页面上切换为“自动/仅 CP-SAT”。
    default_solver: str = 'heuristic'
    compare_solver: str = 'heuristic'
    cpsat_max_time: float = 10.0
    cpsat_workers: int = 0  # 0 表示按 CPU 核数自动决定

    # 会话 Cookie 是否加 Secure（面板开 HTTPS 后建议置 1）
    cookie_secure: bool = False

    # 日志
    access_log: bool = True

    @property
    def worker_count(self) -> int:
        if self.cpsat_workers > 0:
            return self.cpsat_workers
        try:
            cpu = os.cpu_count() or 1
        except Exception:  # pragma: no cover - 极端环境
            cpu = 1
        return max(1, min(4, cpu))

    def as_report(self) -> dict:
        """给 /api/health 与启动自检使用的可读配置。"""
        return {
            'version': VERSION,
            'root': str(self.root),
            'host': self.host,
            'port': self.port,
            'data_dir': str(self.data_dir),
            'static_dir': str(self.static_dir),
            'db_file': str(self.db_file),
            'max_body_bytes': self.max_body_bytes,
            'cookie_secure': self.cookie_secure,
            'max_concurrent_solves': self.max_concurrent_solves,
            'solve_cache_ttl': self.solve_cache_ttl,
            'default_solver': self.default_solver,
            'compare_solver': self.compare_solver,
            'cpsat_max_time': self.cpsat_max_time,
            'cpsat_workers': self.worker_count,
        }


def normalize_solver(value: Optional[str], fallback: str = 'heuristic') -> str:
    """把求解器名称规整为 auto / heuristic / cpsat。"""
    token = str(value or '').strip().lower()
    if token in {'cp-sat', 'cp_sat', 'cpsat'}:
        return 'cpsat'
    if token in {'auto', 'heuristic'}:
        return token
    return fallback


def build_settings(env: Optional[dict] = None) -> Settings:
    """从环境变量构建配置（``env`` 仅用于测试）。"""

    def get(name: str) -> Optional[str]:
        if env is None:
            return os.environ.get(name)
        return env.get(name)

    def s(name: str, default: str) -> str:
        value = get(name)
        if value is None:
            return default
        value = str(value).strip()
        return value or default

    raw_root = s('PLATFORM_ROOT', str(APP_ROOT))
    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        root = (APP_ROOT / root).resolve()

    def i(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
        value = get(name)
        if value is None or not str(value).strip():
            return default
        try:
            out = int(str(value).strip())
        except (TypeError, ValueError):
            return default
        if minimum is not None:
            out = max(minimum, out)
        if maximum is not None:
            out = min(maximum, out)
        return out

    def f(name: str, default: float, minimum: Optional[float] = None) -> float:
        value = get(name)
        if value is None or not str(value).strip():
            return default
        try:
            out = float(str(value).strip())
        except (TypeError, ValueError):
            return default
        if minimum is not None:
            out = max(minimum, out)
        return out

    def b(name: str, default: bool) -> bool:
        value = get(name)
        if value is None:
            return default
        return str(value).strip().lower() not in {'0', 'false', 'no', 'off', ''}

    def p(name: str, default: Path) -> Path:
        value = get(name)
        if value is None or not str(value).strip():
            return default
        path = Path(str(value).strip()).expanduser()
        if not path.is_absolute():
            path = (root / path).resolve()
        return path

    default_solver = normalize_solver(s('PLATFORM_DEFAULT_SOLVER', 'heuristic'))
    compare_solver = normalize_solver(s('PLATFORM_COMPARE_SOLVER', default_solver), default_solver)

    return Settings(
        root=root,
        host=s('PLATFORM_HOST', s('HOST', '127.0.0.1')),
        port=i('PLATFORM_PORT', i('PORT', 8787, 1, 65535), 1, 65535),
        data_dir=p('PLATFORM_DATA_DIR', root / 'data' / 'demo'),
        static_dir=p('PLATFORM_STATIC_DIR', root / 'app' / 'static'),
        db_file=p('PLATFORM_DB', root / 'data' / 'platform.db'),
        max_body_bytes=i('PLATFORM_MAX_BODY_BYTES', 65536, 1024, 16 * 1024 * 1024),
        static_chunk_bytes=i('PLATFORM_STATIC_CHUNK', 65536, 4096, 4 * 1024 * 1024),
        max_concurrent_solves=i('PLATFORM_MAX_CONCURRENT_SOLVES', 2, 1, 64),
        solve_queue_timeout=f('PLATFORM_SOLVE_QUEUE_TIMEOUT', 120.0, 1.0),
        solve_cache_ttl=f('PLATFORM_SOLVE_CACHE_TTL', 300.0, 0.0),
        solve_cache_size=i('PLATFORM_SOLVE_CACHE_SIZE', 64, 0, 4096),
        default_solver=default_solver,
        compare_solver=compare_solver,
        cpsat_max_time=f('PLATFORM_CPSAT_MAX_TIME', 10.0, 0.5),
        cpsat_workers=i('PLATFORM_CPSAT_WORKERS', 0, 0, 64),
        cookie_secure=b('PLATFORM_COOKIE_SECURE', False),
        access_log=b('PLATFORM_ACCESS_LOG', True),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级配置单例。测试可调用 ``get_settings.cache_clear()`` 重置。"""
    return build_settings()


__all__ = [
    'APP_ROOT',
    'VERSION',
    'Settings',
    'build_settings',
    'get_settings',
    'normalize_solver',
]
