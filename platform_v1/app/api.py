"""平台接口层。

这一层只依赖标准库，并且不关心底层是 ``http.server``（server.py）还是
WSGI（wsgi.py，gunicorn/uwsgi/宝塔 Python 项目），因此两种部署方式共用
同一套路由、参数校验、缓存与错误处理。

对外的 URL 契约与原型保持一致：

* ``GET  /api/demo``           演示数据
* ``GET  /api/scenarios``      情景列表
* ``GET  /api/solver/status``  求解器可用性
* ``GET  /api/compare``        五情景对比
* ``POST /api/solve``          生成调度方案

新增（部署运维用，不影响旧前端）：

* ``GET  /api/health``         健康检查，供 Nginx/面板/监控探活
* ``GET  /api/config``         前端默认口径与情景元数据
"""
from __future__ import annotations

import json
import mimetypes
import sqlite3
import threading
import time
import traceback
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app import api_admin, api_monitor, api_plan, auth, db
from app.config import APP_ROOT, VERSION, Settings, get_settings
from app.http import Response
from simulation.scenarios import (
    DEFAULT_WAIT_MIN,
    SCENARIO_IDS,
    SCENARIO_NAMES,
)
from simulation.scheduler import (
    DataFileError,
    data_dir_report,
    load_data_dir,
    solve_scenario,
    solver_status,
)

JSON_CONTENT_TYPE = 'application/json; charset=utf-8'
NO_STORE = 'no-store, must-revalidate'

# 静态资源 MIME（显式声明，避免服务器 mimetypes 数据库缺项）
MIME_OVERRIDES = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.mjs': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.txt': 'text/plain; charset=utf-8',
    '.csv': 'text/csv; charset=utf-8',
    '.map': 'application/json; charset=utf-8',
}

NO_CACHE_FILES = {'index.html'}
STATIC_CACHE_CONTROL = 'public, max-age=600'

# 允许前端传入的求解参数（白名单 + 取值范围）
PARAM_LIMITS = {
    'wait_min': (0, 1440),
    'turnover_min': (0, 240),
    'num_search_workers': (1, 64),
    'max_time_seconds': (0.5, 120.0),
}
SOLVER_CHOICES = {'auto', 'heuristic', 'cpsat', 'cp-sat', 'cp_sat'}


class ApiError(Exception):
    """带 HTTP 状态码的业务错误，最终会序列化成 ``{"error": "..."}``。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# 静态资源
# --------------------------------------------------------------------------
def resolve_static(url_path: str, settings: Optional[Settings] = None) -> Optional[Path]:
    """把 URL 路径映射到 ``static_dir`` 下的真实文件；越权或不存在返回 None。

    只允许访问 static_dir 内部的文件，目录请求不列目录（返回 None → 404）。
    """
    settings = settings or get_settings()
    raw_path = urllib.parse.urlsplit(url_path).path
    try:
        decoded = urllib.parse.unquote(raw_path, errors='strict')
    except Exception:
        return None
    if '\\' in decoded or '\x00' in decoded:
        return None

    rel = decoded.strip('/')
    if not rel:
        rel = 'index.html'

    parts = [part for part in rel.split('/') if part not in ('', '.')]
    if any(part == '..' for part in parts):
        return None

    root = settings.static_dir
    try:
        root_resolved = root.resolve()
        candidate = root_resolved.joinpath(*parts).resolve()
    except (OSError, RuntimeError):
        return None

    if candidate != root_resolved and root_resolved not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in MIME_OVERRIDES:
        return MIME_OVERRIDES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or 'application/octet-stream'


def cache_control_for(path: Path) -> str:
    return NO_STORE if path.name in NO_CACHE_FILES else STATIC_CACHE_CONTROL


# --------------------------------------------------------------------------
# 数据加载（带 mtime 缓存：面板上覆盖 CSV 后刷新页面即可生效）
# --------------------------------------------------------------------------
_data_lock = threading.Lock()
_data_cache: Dict[str, Tuple[tuple, dict, float]] = {}


def _data_signature(data_dir: Path) -> tuple:
    entries = []
    for item in data_dir_report(data_dir):
        entries.append((item['file'], item['exists'], item['size']))
        if item['exists']:
            try:
                entries.append(round((data_dir / item['file']).stat().st_mtime, 3))
            except OSError:
                entries.append(None)
    return tuple(entries)


def load_data(settings: Optional[Settings] = None) -> dict:
    settings = settings or get_settings()
    data_dir = settings.data_dir
    key = str(data_dir)
    signature = _data_signature(data_dir)

    with _data_lock:
        cached = _data_cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]

    data = load_data_dir(data_dir)

    with _data_lock:
        _data_cache[key] = (signature, data, time.time())
    return data


def data_status(settings: Optional[Settings] = None) -> dict:
    settings = settings or get_settings()
    report = data_dir_report(settings.data_dir)
    status = {
        'data_dir': str(settings.data_dir),
        'files': report,
        'ready': all(item['exists'] for item in report),
        'counts': {},
        'error': None,
    }
    try:
        data = load_data(settings)
        status['counts'] = {key: len(rows) for key, rows in data.items()}
    except Exception as exc:
        status['error'] = str(exc)
    return status


# --------------------------------------------------------------------------
# 求解缓存与并发保护
# --------------------------------------------------------------------------
_solve_lock = threading.Lock()
_solve_cache: "OrderedDict[tuple, Tuple[float, dict]]" = OrderedDict()
_solve_sem: Optional[threading.BoundedSemaphore] = None
_solve_sem_size = 0


def _semaphore(settings: Settings) -> threading.BoundedSemaphore:
    global _solve_sem, _solve_sem_size
    with _solve_lock:
        if _solve_sem is None or _solve_sem_size != settings.max_concurrent_solves:
            _solve_sem = threading.BoundedSemaphore(settings.max_concurrent_solves)
            _solve_sem_size = settings.max_concurrent_solves
        return _solve_sem


def _cache_get(key: tuple, settings: Settings) -> Optional[dict]:
    if settings.solve_cache_ttl <= 0 or settings.solve_cache_size <= 0:
        return None
    with _solve_lock:
        hit = _solve_cache.get(key)
        if not hit:
            return None
        if time.time() - hit[0] > settings.solve_cache_ttl:
            _solve_cache.pop(key, None)
            return None
        _solve_cache.move_to_end(key)
        return hit[1]


def _cache_put(key: tuple, value: dict, settings: Settings) -> None:
    if settings.solve_cache_ttl <= 0 or settings.solve_cache_size <= 0:
        return
    with _solve_lock:
        _solve_cache[key] = (time.time(), value)
        _solve_cache.move_to_end(key)
        while len(_solve_cache) > settings.solve_cache_size:
            _solve_cache.popitem(last=False)


def clear_caches() -> None:
    """清空数据与求解缓存（测试与热更新使用）。"""
    with _data_lock:
        _data_cache.clear()
    with _solve_lock:
        _solve_cache.clear()


def _coerce_param(name: str, value: Any) -> Any:
    low, high = PARAM_LIMITS[name]
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ApiError(f'参数 {name} 必须是数字。')
    if number < low:
        number = low
    if number > high:
        number = high
    if name in {'wait_min', 'turnover_min', 'num_search_workers'}:
        return int(number)
    return number


def normalize_params(raw: Any, settings: Settings) -> dict:
    """校验并规整前端传来的求解参数，未知参数直接丢弃。"""
    if raw is None or raw == '':
        return {}
    if not isinstance(raw, dict):
        raise ApiError('params 必须是 JSON 对象。')

    params: Dict[str, Any] = {}
    solver = raw.get('solver')
    if solver is not None and str(solver).strip():
        solver_value = str(solver).strip().lower()
        if solver_value not in SOLVER_CHOICES:
            raise ApiError(f'未知求解器：{solver}')
        params['solver'] = 'cpsat' if solver_value in {'cp-sat', 'cp_sat'} else solver_value

    for name in PARAM_LIMITS:
        if name in raw and raw[name] is not None and raw[name] != '':
            params[name] = _coerce_param(name, raw[name])

    if 'max_time_seconds' in params:
        params['max_time_seconds'] = min(params['max_time_seconds'], settings.cpsat_max_time)
    if 'num_search_workers' in params:
        params['num_search_workers'] = min(params['num_search_workers'], settings.worker_count)
    return params


def run_solver(scenario: str, params: dict, settings: Optional[Settings] = None) -> dict:
    """带缓存、并发限制与统一错误信息的求解调用。"""
    settings = settings or get_settings()
    if scenario not in SCENARIO_IDS:
        raise ApiError(f'未知情景：{scenario}')

    data = load_data(settings)  # 数据错误在这里抛出，转成可读提示
    key = (scenario, json.dumps(params, sort_keys=True, ensure_ascii=False), _data_signature(settings.data_dir))

    cached = _cache_get(key, settings)
    if cached is not None:
        result = dict(cached)
        result['cached'] = True
        return result

    sem = _semaphore(settings)
    acquired = sem.acquire(timeout=settings.solve_queue_timeout)
    if not acquired:
        raise ApiError('求解任务排队超时，请稍后重试（可调大 PLATFORM_MAX_CONCURRENT_SOLVES）。', status=503)
    try:
        # 双重检查：等锁期间可能已有其他请求写入缓存
        cached = _cache_get(key, settings)
        if cached is not None:
            result = dict(cached)
            result['cached'] = True
            return result
        result = solve_scenario(scenario, data, params)
    finally:
        sem.release()

    _cache_put(key, result, settings)
    return result


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------
def _scenario_payload() -> dict:
    return {
        'scenarios': list(SCENARIO_IDS),
        'names': dict(SCENARIO_NAMES),
        'defaults': dict(DEFAULT_WAIT_MIN),
    }


def _solver_status_payload(settings: Settings) -> dict:
    status = dict(solver_status())
    status['default_engine'] = settings.default_solver
    status['compare_engine'] = settings.compare_solver
    status['max_time_seconds'] = settings.cpsat_max_time
    status['workers'] = settings.worker_count
    return status


def _health_payload(settings: Settings) -> dict:
    status = solver_status()
    data = data_status(settings)
    healthy = bool(data.get('ready')) and bool(data.get('counts'))
    return {
        'status': 'ok' if healthy else 'degraded',
        'version': VERSION,
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'uptime_seconds': round(time.time() - _STARTED_AT, 1),
        'solver': {
            'cp_sat_ready': status.get('cp_sat_ready', False),
            'ortools_version': status.get('ortools_version'),
            'default_engine': settings.default_solver,
        },
        'data': {
            'ready': data.get('ready'),
            'data_dir': data.get('data_dir'),
            'counts': data.get('counts'),
            'error': data.get('error'),
        },
    }


def _compare_items(settings: Settings, solver: str, wait_overrides: Dict[str, int]) -> dict:
    items = []
    for scenario in SCENARIO_IDS:
        params: Dict[str, Any] = {
            'solver': solver,
            'wait_min': wait_overrides.get(scenario, DEFAULT_WAIT_MIN[scenario]),
            'max_time_seconds': settings.cpsat_max_time,
            'num_search_workers': settings.worker_count,
        }
        result = run_solver(scenario, params, settings)
        items.append({
            'scenario': scenario,
            'kpi': result.get('kpi', {}),
            'solver': result.get('solver', 'unknown'),
        })
    return {'items': items, 'solver': solver}


def handle_api(method: str, target: str, body: bytes = b'',
               settings: Optional[Settings] = None,
               headers: Optional[dict] = None) -> Response:
    """API 统一入口。

    返回 :class:`app.http.Response`（状态码 + JSON 载荷 + 响应头）。
    管理类接口（登录、客户、车辆、参数、用户、留痕、距离校核）在
    ``app/api_admin.py`` 中实现，这里只负责演示/求解接口与统一错误处理。
    """
    settings = settings or get_settings()
    method = (method or 'GET').upper()
    parts = urllib.parse.urlsplit(target)
    path = parts.path.rstrip('/') or '/'
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=False)

    try:
        if not path.startswith('/api/'):
            raise ApiError(f'未知接口：{path}', status=404)

        if path.startswith('/api/auth/') or path.startswith('/api/customers') or \
                path.startswith('/api/vehicles') or path.startswith('/api/params') or \
                path.startswith('/api/users') or path.startswith('/api/audit') or \
                path.startswith('/api/geo') or path.startswith('/api/plan') or \
                path.startswith('/api/monitor'):
            if path.startswith('/api/monitor'):
                monitor_response = api_monitor.handle(method, target, body, query, headers or {}, settings)
                if monitor_response is not None:
                    return monitor_response
            if path.startswith('/api/plan'):
                plan_response = api_plan.handle(method, target, body, query, headers or {}, settings)
                if plan_response is not None:
                    return plan_response
            admin_response = api_admin.handle(method, target, body, query, headers or {}, settings)
            if admin_response is not None:
                return admin_response

        if method in {'GET', 'HEAD'}:
            if path == '/api/health':
                return Response(200, _health_payload(settings))
            if path == '/api/config':
                payload = _scenario_payload()
                payload.update({
                    'version': VERSION,
                    'default_solver': settings.default_solver,
                    'compare_solver': settings.compare_solver,
                })
                return Response(200, payload)
            if path == '/api/demo':
                return Response(200, load_data(settings))
            if path == '/api/scenarios':
                return Response(200, _scenario_payload())
            if path == '/api/solver/status':
                return Response(200, _solver_status_payload(settings))
            if path == '/api/compare':
                solver = str((query.get('solver') or [settings.compare_solver])[0]).strip().lower()
                if solver in {'cp-sat', 'cp_sat'}:
                    solver = 'cpsat'
                if solver not in {'auto', 'heuristic', 'cpsat'}:
                    solver = settings.compare_solver
                return Response(200, _compare_items(settings, solver, {}))
            raise ApiError(f'未知接口：{path}', status=404)

        if method == 'POST':
            if path != '/api/solve':
                raise ApiError(f'未知接口：{path}', status=404)
            payload = _decode_body(body, settings)
            scenario = str(payload.get('scenario') or 'D3').strip()
            params = normalize_params(payload.get('params'), settings)
            params.setdefault('solver', 'auto')
            return Response(200, run_solver(scenario, params, settings))

        raise ApiError(f'不支持的请求方法：{method}', status=405)

    except api_admin.ApiProblem as exc:
        return Response(exc.status, {'error': exc.message})
    except auth.AuthError as exc:
        return Response(exc.status, {'error': exc.message})
    except ApiError as exc:
        return Response(exc.status, {'error': exc.message})
    except DataFileError as exc:
        return Response(500, {'error': f'输入数据有问题：{exc}'})
    except ValueError as exc:
        return Response(400, {'error': str(exc)})
    except sqlite3.Error as exc:
        return Response(500, {'error': f'数据库错误：{exc}'})
    except Exception as exc:  # pragma: no cover - 兜底
        traceback.print_exc()
        return Response(500, {'error': f'服务器内部错误：{exc}'})


def _decode_body(body: bytes, settings: Settings) -> dict:
    if not body:
        raise ApiError('请求体为空，需要 JSON：{"scenario": "D3", "params": {...}}')
    if len(body) > settings.max_body_bytes:
        raise ApiError(f'请求体过大（上限 {settings.max_body_bytes} 字节）。', status=413)
    try:
        payload = json.loads(body.decode('utf-8'))
    except UnicodeDecodeError:
        raise ApiError('请求体必须是 UTF-8 编码的 JSON。')
    except json.JSONDecodeError as exc:
        raise ApiError(f'JSON 解析失败：{exc}')
    if not isinstance(payload, dict):
        raise ApiError('请求体必须是 JSON 对象。')
    return payload


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


# --------------------------------------------------------------------------
# 启动自检
# --------------------------------------------------------------------------
_STARTED_AT = time.time()


def startup_check(settings: Optional[Settings] = None) -> Tuple[bool, list]:
    """启动前自检：Python 版本、静态目录、输入数据、OR-Tools 状态。"""
    import sys

    settings = settings or get_settings()
    lines = []
    ok = True

    py = sys.version_info
    lines.append(('信息', f'Python {py.major}.{py.minor}.{py.micro}（{sys.executable}）'))
    if py < (3, 8):
        ok = False
        lines.append(('错误', '需要 Python 3.8 及以上版本。'))

    index = settings.static_dir / 'index.html'
    if index.is_file():
        lines.append(('信息', f'静态目录：{settings.static_dir}'))
    else:
        ok = False
        lines.append(('错误', f'找不到前端页面：{index}'))

    data = data_status(settings)
    if data['ready']:
        counts = '、'.join(f'{k} {v}' for k, v in (data.get('counts') or {}).items())
        lines.append(('信息', f'数据目录：{data["data_dir"]}（{counts or "未读取"}）'))
    else:
        ok = False
        missing = '、'.join(item['file'] for item in data['files'] if not item['exists'])
        lines.append(('错误', f'数据目录 {data["data_dir"]} 缺少文件：{missing or "读取失败"}'))
    if data.get('error'):
        ok = False
        lines.append(('错误', f'数据读取失败：{data["error"]}'))

    status = solver_status()
    if status.get('cp_sat_ready'):
        lines.append(('信息', f"CP-SAT 可用：OR-Tools {status.get('ortools_version')}"))
    else:
        lines.append(('提示', 'CP-SAT 不可用，平台将使用启发式求解器（功能正常）。'))

    lines.append(('信息', f'页面默认口径：{settings.default_solver}；对比口径：{settings.compare_solver}'))

    try:
        conn = db.connect(settings)
        counts = db.table_counts(conn)
        conn.close()
        lines.append(('信息', f'数据库：{settings.db_file}（客户 {counts.get("customers", 0)}、'
                              f'车辆 {counts.get("vehicles", 0)}、参数 {counts.get("params", 0)}、'
                              f'用户 {counts.get("users", 0)}）'))
        if not counts.get('users'):
            ok = False
            lines.append(('错误', '还没有任何用户账号，无法登录（重启服务会自动创建 admin）。'))
    except Exception as exc:  # pragma: no cover
        ok = False
        lines.append(('错误', f'数据库不可用：{exc}'))

    return ok, lines


__all__ = [
    'ApiError',
    'JSON_CONTENT_TYPE',
    'Response',
    'NO_STORE',
    'cache_control_for',
    'clear_caches',
    'content_type_for',
    'data_status',
    'handle_api',
    'json_bytes',
    'load_data',
    'normalize_params',
    'resolve_static',
    'run_solver',
    'startup_check',
]
