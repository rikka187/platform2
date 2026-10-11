#!/usr/bin/env bash
# =============================================================================
# 银犁物流仓配一体化协同平台 —— 部署体检脚本
#
#   bash deploy/check.sh
#
# 逐项检查：系统与 Python、虚拟环境与依赖、数据文件、启动自检、
# systemd 服务、端口监听、健康接口、Nginx 配置、防火墙与日志。
# 出问题时按输出顺序自下而上排查即可。
# =============================================================================
set -uo pipefail

if grep -q $'\r' "$0" 2>/dev/null; then
  echo "错误：检测到 Windows 换行符(CRLF)。请先执行： sed -i 's/\\r\$//' deploy/*.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/platform.env"
SERVICE_NAME="${SERVICE_NAME:-yinli-platform}"
VENV_PY="$APP_DIR/.venv/bin/python"

PASS=0; WARN=0; FAIL=0
ok()   { printf '\033[32m  [通过]\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
warn() { printf '\033[33m  [注意]\033[0m %s\n' "$*"; WARN=$((WARN+1)); }
bad()  { printf '\033[31m  [失败]\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
section() { printf '\n\033[36m== %s ==\033[0m\n' "$*"; }

section "1. 系统与 Python"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  ok "系统：${PRETTY_NAME:-unknown}"
fi
if command -v python3 >/dev/null 2>&1; then
  PYV="$(python3 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"
  if python3 -c 'import sys;exit(0 if sys.version_info>=(3,8) else 1)'; then
    ok "python3 $PYV"
  else
    bad "python3 版本过低（$PYV），需要 3.8+"
  fi
else
  bad "未找到 python3"
fi
if [[ -x "$VENV_PY" ]]; then
  ok "虚拟环境：$VENV_PY（$("$VENV_PY" -V 2>&1)）"
else
  warn "未找到虚拟环境 $APP_DIR/.venv，请先运行 sudo bash deploy/install.sh"
fi

section "2. 依赖与求解器"
CHECK_PY="$VENV_PY"
[[ -x "$CHECK_PY" ]] || CHECK_PY="$(command -v python3 || true)"
if [[ -n "$CHECK_PY" ]]; then
  if "$CHECK_PY" -c 'from ortools.sat.python import cp_model' >/dev/null 2>&1; then
    ok "OR-Tools CP-SAT 可用：$("$CHECK_PY" -c 'import ortools;print(getattr(ortools,"__version__","unknown"))' 2>/dev/null)"
  else
    warn "OR-Tools 不可用（平台仍可用启发式求解；“仅 CP-SAT”会提示安装）"
  fi
fi

section "3. 数据文件"
DATA_DIR="$APP_DIR/data/demo"
if [[ -f "$ENV_FILE" ]]; then
  ENV_DATA_DIR="$(grep -E '^PLATFORM_DATA_DIR=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  [[ -n "${ENV_DATA_DIR:-}" ]] && DATA_DIR="$ENV_DATA_DIR"
fi
MISSING=""
for f in orders.csv trips.csv vehicles.csv docks.csv prep.csv charging.csv; do
  [[ -f "$DATA_DIR/$f" ]] || MISSING="$MISSING $f"
done
if [[ -z "$MISSING" ]]; then
  ok "数据目录：$DATA_DIR（6 个 CSV 齐全）"
else
  bad "数据目录 $DATA_DIR 缺少：$MISSING"
fi

section "4. 启动自检（代码 + 数据 + 编码）"
if [[ -n "${CHECK_PY:-}" ]]; then
  if "$CHECK_PY" "$APP_DIR/server.py" --check-only >/tmp/platform_check.log 2>&1; then
    ok "server.py --check-only 通过"
  else
    bad "server.py --check-only 未通过："
    sed 's/^/       /' /tmp/platform_check.log
  fi
fi

section "5. systemd 服务"
if command -v systemctl >/dev/null 2>&1; then
  # 注意：不要写成 `systemctl list-unit-files | grep -q ...`。本脚本启用了
  # pipefail，grep -q 命中后会提前退出，systemctl 因 SIGPIPE 返回 141，
  # 整个管道被判为失败，于是“服务已注册”会被误报成“未注册”。
  if systemctl cat "$SERVICE_NAME" >/dev/null 2>&1 || [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
    STATE="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    ENABLED="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    if [[ "$STATE" == "active" ]]; then
      ok "服务 $SERVICE_NAME 正在运行（开机自启：${ENABLED:-unknown}）"
    else
      bad "服务 $SERVICE_NAME 状态：${STATE:-unknown}，用 journalctl -u $SERVICE_NAME -n 50 查看原因"
    fi
  else
    warn "未注册 systemd 服务 $SERVICE_NAME（可用 sudo bash deploy/install.sh 安装）"
  fi
else
  warn "系统没有 systemd，跳过服务检查"
fi

section "6. 端口监听"
PORT="8787"; HOST="127.0.0.1"
if [[ -f "$ENV_FILE" ]]; then
  ENV_PORT="$(grep -E '^PLATFORM_PORT=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  ENV_HOST="$(grep -E '^PLATFORM_HOST=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  [[ -n "${ENV_PORT:-}" ]] && PORT="$ENV_PORT"
  [[ -n "${ENV_HOST:-}" ]] && HOST="$ENV_HOST"
fi
if command -v ss >/dev/null 2>&1; then
  # 同样避免 grep -q 触发 SIGPIPE：这里用重定向到 /dev/null，让 grep 读完输入。
  if ss -ltn 2>/dev/null | grep -E "[:.]${PORT}\b" >/dev/null; then
    ok "端口 $PORT 正在监听"
    if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" ]]; then
      warn "服务监听在 $HOST:$PORT（对公网开放），生产环境建议改成 127.0.0.1 并由 Nginx 代理"
    fi
  else
    bad "端口 $PORT 没有监听"
  fi
fi

section "7. 健康接口"
if command -v curl >/dev/null 2>&1; then
  BODY="$(curl -fsS --max-time 8 "http://127.0.0.1:${PORT}/api/health" 2>/dev/null || true)"
  if [[ -n "$BODY" ]]; then
    ok "GET /api/health → $BODY"
  else
    bad "无法访问 http://127.0.0.1:${PORT}/api/health"
  fi
  HOME_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/" 2>/dev/null || true)"
  [[ "$HOME_CODE" == "200" ]] && ok "GET / → 200" || bad "GET / → ${HOME_CODE:-无响应}"

  # 新版接口探活：升级后若这里仍是 404，说明进程还在跑旧代码，需要重启服务
  AUTH_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/api/auth/me" 2>/dev/null || true)"
  if [[ "$AUTH_CODE" == "401" ]]; then
    ok "GET /api/auth/me → 401（新版登录接口已生效）"
  elif [[ "$AUTH_CODE" == "404" ]]; then
    bad "GET /api/auth/me → 404：服务仍在运行旧代码，请执行 systemctl restart ${SERVICE_NAME}"
  else
    bad "GET /api/auth/me → ${AUTH_CODE:-无响应}"
  fi
  LOGIN_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/login.html" 2>/dev/null || true)"
  [[ "$LOGIN_CODE" == "200" ]] && ok "GET /login.html → 200" || bad "GET /login.html → ${LOGIN_CODE:-无响应}"
else
  warn "未安装 curl，跳过接口探测"
fi

section "7.1 数据层（SQLite）"
DATA_DIR="${PLATFORM_DB_DIR:-$APP_DIR/data}"
DB_FILE="${PLATFORM_DB:-$DATA_DIR/platform.db}"
if [[ -d "$DATA_DIR" ]]; then
  if [[ "$SERVICE_USER" == "root" ]] || su -s /bin/sh "$SERVICE_USER" -c "test -w '$DATA_DIR'" 2>/dev/null; then
    ok "数据目录可写：$DATA_DIR（运行用户 ${SERVICE_USER:-root}）"
  else
    bad "数据目录对运行用户 ${SERVICE_USER:-root} 不可写：$DATA_DIR（会导致所有接口 500）"
    warn "修复：chown -R ${SERVICE_USER:-root}:${SERVICE_GROUP:-root} $DATA_DIR"
  fi
else
  bad "数据目录不存在：$DATA_DIR"
fi
if [[ -f "$DB_FILE" ]]; then
  ok "数据库文件存在：$DB_FILE（$(du -h "$DB_FILE" 2>/dev/null | cut -f1)）"
else
  warn "数据库文件尚未生成：$DB_FILE（服务首次启动会自动建库并导入种子数据）"
fi
# 复用应用自带的自检（server.py --check-only 会打印各表计数并以「自检结果：通过」收尾）
CHECK_PY="${VENV_DIR:-$APP_DIR/.venv}/bin/python"
if [[ -x "$CHECK_PY" && -f "$APP_DIR/server.py" ]]; then
  CHECK_OUT="$("$CHECK_PY" "$APP_DIR/server.py" --check-only 2>&1 || true)"
  if printf '%s' "$CHECK_OUT" | grep -q '自检结果：通过'; then
    ok "应用自检通过：$(printf '%s' "$CHECK_OUT" | grep -m1 '数据库：' || true)"
  else
    warn "应用自检未通过，可手动查看：cd $APP_DIR && $CHECK_PY server.py --check-only"
    printf '%s\n' "$CHECK_OUT" | tail -n 5 | sed 's/^/       /'
  fi
else
  warn "未找到虚拟环境解释器 $CHECK_PY，跳过应用自检"
fi

section "8. Nginx 反向代理"
BAOTA_NGINX="/www/server/nginx/sbin/nginx"
if [[ -x "$BAOTA_NGINX" ]]; then
  if "$BAOTA_NGINX" -t >/tmp/nginx_check.log 2>&1; then
    ok "宝塔 Nginx 配置校验通过"
  else
    bad "宝塔 Nginx 配置有误："; sed 's/^/       /' /tmp/nginx_check.log
  fi
  if grep -rqs "127.0.0.1:${PORT}" /www/server/panel/vhost/nginx/ 2>/dev/null; then
    ok "已有站点反向代理到 127.0.0.1:${PORT}"
  else
    warn "没有站点反向代理到 127.0.0.1:${PORT}，请在面板添加反向代理"
  fi
  if grep -rqsF '.(js|css)' /www/server/panel/vhost/nginx/ 2>/dev/null; then
    warn "检测到面板默认的 js/css 静态规则，可能导致 /app.js、/styles.css 404，请按部署文档删除"
  fi
elif command -v nginx >/dev/null 2>&1; then
  nginx -t >/tmp/nginx_check.log 2>&1 && ok "系统 Nginx 配置校验通过" || { bad "Nginx 配置有误："; sed 's/^/       /' /tmp/nginx_check.log; }
else
  warn "未检测到 Nginx"
fi

section "9. 防火墙与磁盘"
if command -v ufw >/dev/null 2>&1; then
  UFW_STATE="$(ufw status 2>/dev/null | head -n1 || true)"
  ok "ufw：${UFW_STATE:-unknown}（云服务器还需在安全组放行 80/443）"
fi
DF_LINE="$(df -h "$APP_DIR" 2>/dev/null | tail -n1 || true)"
[[ -n "$DF_LINE" ]] && ok "磁盘：$DF_LINE"

section "10. 最近日志（最近 15 行）"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -u "$SERVICE_NAME" -n 15 --no-pager 2>/dev/null | sed 's/^/  /' || warn "暂无日志"
fi

printf '\n\033[36m== 体检结果 ==\033[0m\n'
printf '  通过 %d 项，注意 %d 项，失败 %d 项\n' "$PASS" "$WARN" "$FAIL"
if [[ $FAIL -gt 0 ]]; then
  printf '  \033[31m存在失败项，请按上面的提示处理；详细部署步骤见 docs/部署到Ubuntu宝塔面板.md\033[0m\n'
  exit 1
fi
printf '  \033[32m关键项全部通过。\033[0m\n'
