"""登录、会话、角色权限与操作留痕。

对应论文 4.3.5"权限控制：按调度、仓储、车队、管理等角色分配读写权限，
关键操作留痕"。

- 口令：PBKDF2-HMAC-SHA256，20 万次迭代，每用户独立随机盐；
- 会话：随机 token 存 ``sessions`` 表，默认 12 小时过期，浏览器只拿 HttpOnly Cookie；
- 角色：admin / dispatcher / warehouse / fleet / viewer（中文名称见 ROLE_LABELS）；
- 留痕：所有写操作与登录事件写入 ``audit_log``。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from app.db import audit, now

PBKDF2_ROUNDS = 200_000
SESSION_HOURS = 12
COOKIE_NAME = 'platform_session'
MIN_PASSWORD_LEN = 8

ROLE_ADMIN = 'admin'
ROLE_DISPATCHER = 'dispatcher'
ROLE_WAREHOUSE = 'warehouse'
ROLE_FLEET = 'fleet'
ROLE_VIEWER = 'viewer'

ROLE_LABELS = {
    ROLE_ADMIN: '管理员',
    ROLE_DISPATCHER: '调度员',
    ROLE_WAREHOUSE: '仓储',
    ROLE_FLEET: '车队',
    ROLE_VIEWER: '只读',
}

# 权限点：模块.动作
PERMISSIONS = {
    ROLE_ADMIN: {'*'},
    ROLE_DISPATCHER: {
        'dashboard.view', 'customers.view', 'customers.edit', 'plan.view', 'plan.run',
        'docks.view', 'docks.edit', 'scenarios.view', 'scenarios.run', 'monitor.view',
        'monitor.handle',
    },
    ROLE_WAREHOUSE: {
        'dashboard.view', 'customers.view', 'docks.view', 'docks.edit',
        'scenarios.view', 'monitor.view', 'monitor.handle',
    },
    ROLE_FLEET: {
        'dashboard.view', 'vehicles.view', 'vehicles.edit', 'energy.view', 'energy.edit',
        'scenarios.view', 'monitor.view', 'monitor.handle',
    },
    ROLE_VIEWER: {
        'dashboard.view', 'customers.view', 'vehicles.view', 'plan.view',
        'docks.view', 'energy.view', 'scenarios.view', 'monitor.view',
    },
}


class AuthError(Exception):
    """认证/授权失败，携带 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def hash_password(password: str, salt: Optional[str] = None) -> tuple:
    """返回 (hash_hex, salt_hex)。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    try:
        candidate, _ = hash_password(password, salt)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, password_hash)


def validate_password(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f'口令至少 {MIN_PASSWORD_LEN} 位。', status=400)
    if password.isdigit() or password.isalpha():
        raise AuthError('口令需同时包含字母与数字（或符号）。', status=400)


def has_permission(role: str, permission: str) -> bool:
    allowed = PERMISSIONS.get(role, set())
    if '*' in allowed:
        return True
    if permission in allowed:
        return True
    # 允许 customers.* 这类通配
    head = permission.split('.')[0]
    return f'{head}.*' in allowed


def require(user: Optional[dict], permission: str) -> dict:
    if not user:
        raise AuthError('请先登录。', status=401)
    if not has_permission(user.get('role', ''), permission):
        raise AuthError(f'当前角色（{ROLE_LABELS.get(user.get("role", ""), user.get("role", ""))}）无此权限。', status=403)
    return user


# --------------------------------------------------------------------------
# 用户
# --------------------------------------------------------------------------
def user_count(conn: sqlite3.Connection) -> int:
    return conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str,
                display_name: str = '', must_change: bool = False,
                operator: Optional[dict] = None) -> dict:
    username = (username or '').strip()
    if not username:
        raise AuthError('用户名不能为空。', status=400)
    if role not in ROLE_LABELS:
        raise AuthError(f'未知角色：{role}', status=400)
    validate_password(password)
    if conn.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
        raise AuthError(f'用户名已存在：{username}', status=400)
    digest, salt = hash_password(password)
    cur = conn.execute(
        '''INSERT INTO users (username, password_hash, salt, role, display_name, active,
                              must_change_password, created_at)
           VALUES (?,?,?,?,?,1,?,?)''',
        (username, digest, salt, role, display_name or username, 1 if must_change else 0, now()),
    )
    audit(conn, 'user.create', 'user', str(cur.lastrowid),
          f'新建用户 {username}（{ROLE_LABELS[role]}）', operator)
    return get_user(conn, cur.lastrowid)


def get_user(conn: sqlite3.Connection, user_id: int) -> Optional[dict]:
    row = conn.execute(
        'SELECT id, username, role, display_name, active, must_change_password, created_at, last_login_at '
        'FROM users WHERE id = ?', (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_name(conn: sqlite3.Connection, username: str) -> Optional[dict]:
    row = conn.execute('SELECT * FROM users WHERE username = ?', (username.strip(),)).fetchone()
    return dict(row) if row else None


def list_users(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        'SELECT id, username, role, display_name, active, must_change_password, created_at, last_login_at '
        'FROM users ORDER BY id')
    return [dict(row) for row in rows]


def set_password(conn: sqlite3.Connection, user_id: int, password: str,
                 must_change: bool = False, operator: Optional[dict] = None) -> None:
    validate_password(password)
    digest, salt = hash_password(password)
    conn.execute('UPDATE users SET password_hash = ?, salt = ?, must_change_password = ? WHERE id = ?',
                 (digest, salt, 1 if must_change else 0, user_id))
    audit(conn, 'user.password', 'user', str(user_id), '修改口令', operator)


def set_active(conn: sqlite3.Connection, user_id: int, active: bool, operator: Optional[dict] = None) -> None:
    conn.execute('UPDATE users SET active = ? WHERE id = ?', (1 if active else 0, user_id))
    audit(conn, 'user.active', 'user', str(user_id), '启用' if active else '停用', operator)


def set_role(conn: sqlite3.Connection, user_id: int, role: str, operator: Optional[dict] = None) -> None:
    if role not in ROLE_LABELS:
        raise AuthError(f'未知角色：{role}', status=400)
    conn.execute('UPDATE users SET role = ? WHERE id = ?', (role, user_id))
    audit(conn, 'user.role', 'user', str(user_id), f'角色改为 {ROLE_LABELS[role]}', operator)


def authenticate(conn: sqlite3.Connection, username: str, password: str, ip: str = '') -> dict:
    row = get_user_by_name(conn, username or '')
    if not row or not row.get('active'):
        audit(conn, 'login.fail', 'user', username or '', '用户不存在或已停用', None, ip)
        raise AuthError('用户名或口令错误。', status=401)
    if not verify_password(password or '', row['password_hash'], row['salt']):
        audit(conn, 'login.fail', 'user', row['username'], '口令错误',
              {'id': row['id'], 'username': row['username'], 'role': row['role']}, ip)
        raise AuthError('用户名或口令错误。', status=401)
    conn.execute('UPDATE users SET last_login_at = ? WHERE id = ?', (now(), row['id']))
    user = get_user(conn, row['id'])
    audit(conn, 'login.ok', 'user', row['username'], '登录成功', user, ip)
    return user


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------
def create_session(conn: sqlite3.Connection, user_id: int, ip: str = '',
                   user_agent: str = '', hours: int = SESSION_HOURS) -> str:
    token = secrets.token_urlsafe(32)
    created = datetime.now()
    expires = created + timedelta(hours=hours)
    conn.execute(
        'INSERT INTO sessions (token, user_id, created_at, expires_at, ip, user_agent) VALUES (?,?,?,?,?,?)',
        (token, user_id, created.strftime('%Y-%m-%d %H:%M:%S'),
         expires.strftime('%Y-%m-%d %H:%M:%S'), ip, (user_agent or '')[:200]),
    )
    return token


def get_session_user(conn: sqlite3.Connection, token: str) -> Optional[dict]:
    if not token:
        return None
    row = conn.execute(
        '''SELECT s.token, s.expires_at, u.id, u.username, u.role, u.display_name, u.active,
                  u.must_change_password
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ?''', (token,)).fetchone()
    if not row:
        return None
    if row['expires_at'] < datetime.now().strftime('%Y-%m-%d %H:%M:%S'):
        conn.execute('DELETE FROM sessions WHERE token = ?', (token,))
        return None
    if not row['active']:
        return None
    return {'id': row['id'], 'username': row['username'], 'role': row['role'],
            'display_name': row['display_name'],
            'must_change_password': row['must_change_password']}


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    if token:
        conn.execute('DELETE FROM sessions WHERE token = ?', (token,))


def purge_expired_sessions(conn: sqlite3.Connection) -> int:
    cur = conn.execute('DELETE FROM sessions WHERE expires_at < ?',
                       (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
    return cur.rowcount or 0


# --------------------------------------------------------------------------
# 初始化管理员
# --------------------------------------------------------------------------
def ensure_default_admin(conn: sqlite3.Connection, username: str = 'admin',
                         password: str = 'admin123') -> Optional[dict]:
    """首次运行时创建管理员账号；已存在用户则不动。"""
    if user_count(conn) > 0:
        return None
    user = create_user(conn, username, password, ROLE_ADMIN, '系统管理员', must_change=True)
    audit(conn, 'user.bootstrap', 'user', str(user['id']),
          f'初始化管理员 {username}（首次登录需改口令）', None)
    return user


def bootstrap(settings=None) -> dict:
    """启动时调用：建库建表 + 首次导入案例2 种子 + 创建默认管理员。

    返回 ``{'counts': {...}, 'admin': 新建的管理员 or None}``。
    """
    from app import db

    conn = db.connect(settings)
    try:
        db.init_db(settings)
        admin = ensure_default_admin(conn)
        return {'counts': db.table_counts(conn), 'admin': admin}
    finally:
        conn.close()


__all__ = [
    'AuthError', 'COOKIE_NAME', 'MIN_PASSWORD_LEN', 'PERMISSIONS', 'ROLE_ADMIN',
    'ROLE_DISPATCHER', 'ROLE_FLEET', 'ROLE_LABELS', 'ROLE_VIEWER', 'ROLE_WAREHOUSE',
    'SESSION_HOURS', 'authenticate', 'bootstrap', 'create_session', 'create_user',
    'delete_session', 'ensure_default_admin', 'get_session_user', 'get_user',
    'get_user_by_name', 'has_permission', 'list_users', 'purge_expired_sessions',
    'require', 'set_active', 'set_password', 'set_role', 'user_count',
    'validate_password', 'verify_password',
]
