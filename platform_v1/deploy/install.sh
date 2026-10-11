#!/usr/bin/env bash
# =============================================================================
# 银犁物流仓配一体化协同平台 —— Ubuntu 20.04 / 宝塔面板 一键部署脚本
#
# 典型用法（在服务器上、项目目录内执行）：
#   cd /www/wwwroot/platform_v1
#   sed -i 's/\r$//' deploy/*.sh          # 若从 Windows 上传过，先去掉 CRLF
#   sudo bash deploy/install.sh
#
# 脚本会完成：
#   1. 检查系统与 Python 版本（Ubuntu 20.04 自带 3.8，满足要求）
#   2. 在项目目录创建独立虚拟环境 .venv 并安装依赖（含 OR-Tools，失败自动换镜像）
#   3. 生成 deploy/platform.env 配置（已存在则不覆盖）
#   4. 注册并启动 systemd 服务，只监听 127.0.0.1，由 Nginx 反向代理对外
#   5. 启动后自动做健康检查并打印后续步骤
#
# 常用参数见：sudo bash deploy/install.sh --help
# =============================================================================
set -Eeuo pipefail

# ---- Windows 换行符自检 ------------------------------------------------------
if grep -q $'\r' "$0" 2>/dev/null; then
  echo "错误：检测到 Windows 换行符(CRLF)，bash 无法正确执行本脚本。" >&2
  echo "请先执行： sed -i 's/\\r\$//' deploy/*.sh  然后重试。" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR"
VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$SCRIPT_DIR/platform.env"
ENV_EXAMPLE="$SCRIPT_DIR/platform.env.example"
UNIT_TEMPLATE="$SCRIPT_DIR/platform.service"
UNIT_PATH=""

SERVICE_NAME="yinli-platform"
SERVICE_USER="root"
SERVICE_GROUP="root"
PYTHON_BIN="python3"
WITH_NGINX=""
NGINX_DOMAIN=""
SKIP_APT=0
SKIP_DEPS=0
DO_START=1
DO_UNINSTALL=0
PURGE_VENV=0
FORCE_ENV=0

PIP_INDEXES=(
  ""
  "https://pypi.tuna.tsinghua.edu.cn/simple"
  "https://mirrors.aliyun.com/pypi/simple"
)

# ---- 输出工具 ----------------------------------------------------------------
c_info()  { printf '\033[32m[信息]\033[0m %s\n' "$*"; }
c_warn()  { printf '\033[33m[提示]\033[0m %s\n' "$*"; }
c_err()   { printf '\033[31m[错误]\033[0m %s\n' "$*" >&2; }
c_step()  { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()     { c_err "$*"; exit 1; }

usage() {
  cat <<'EOF'
用法：sudo bash deploy/install.sh [选项]

选项：
  --port PORT           服务监听端口（默认 8787）
  --host ADDR           服务监听地址（默认 127.0.0.1，生产环境不要改）
  --user USER           运行服务的系统用户（默认 root；可用 www）
  --service-name NAME   systemd 服务名（默认 yinli-platform）
  --python BIN          指定 python3 解释器（默认 python3）
  --with-nginx DOMAIN   同时写入 Nginx 反向代理配置（如 demo.example.com）
  --skip-apt            不执行 apt 安装（离线或已装好依赖时使用）
  --skip-deps           不重新安装 Python 依赖（复用已有 .venv）
  --force-env           覆盖已存在的 deploy/platform.env
  --no-start            只安装不启动服务
  --uninstall           停止并移除 systemd 服务（保留项目文件）
  --purge-venv          与 --uninstall 一起用，同时删除 .venv
  -h, --help            显示本帮助

示例：
  sudo bash deploy/install.sh
  sudo bash deploy/install.sh --port 9000 --with-nginx demo.example.com
  sudo bash deploy/install.sh --user www
EOF
}

# ---- 参数解析 ----------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)         PORT_OVERRIDE="${2:-}"; shift 2 ;;
    --host)         HOST_OVERRIDE="${2:-}"; shift 2 ;;
    --user)         SERVICE_USER="${2:-}"; shift 2 ;;
    --service-name) SERVICE_NAME="${2:-}"; shift 2 ;;
    --python)       PYTHON_BIN="${2:-}"; shift 2 ;;
    --with-nginx)   WITH_NGINX=1; NGINX_DOMAIN="${2:-}"; shift 2 ;;
    --skip-apt)     SKIP_APT=1; shift ;;
    --skip-deps)    SKIP_DEPS=1; shift ;;
    --force-env)    FORCE_ENV=1; shift ;;
    --no-start)     DO_START=0; shift ;;
    --uninstall)    DO_UNINSTALL=1; shift ;;
    --purge-venv)   PURGE_VENV=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              die "未知参数：$1（用 --help 查看用法）" ;;
  esac
done

PORT_OVERRIDE="${PORT_OVERRIDE:-}"
HOST_OVERRIDE="${HOST_OVERRIDE:-}"

[[ $EUID -eq 0 ]] || die "请用 root 执行：sudo bash deploy/install.sh"

# ---- 卸载模式 ----------------------------------------------------------------
if [[ $DO_UNINSTALL -eq 1 ]]; then
  c_step "卸载服务 $SERVICE_NAME"
  # 不要用 `systemctl list-unit-files | grep -q`：脚本启用了 pipefail，
  # grep -q 提前退出会让 systemctl 收到 SIGPIPE 返回 141，从而误判“未注册”。
  if systemctl cat "$SERVICE_NAME" >/dev/null 2>&1 || [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
  fi
  rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true
  c_info "已移除 systemd 服务：$SERVICE_NAME"
  if [[ $PURGE_VENV -eq 1 && -d "$VENV_DIR" ]]; then
    rm -rf "$VENV_DIR"
    c_info "已删除虚拟环境：$VENV_DIR"
  fi
  c_info "项目文件与 platform.env 均保留，可重新执行 install.sh 部署。"
  exit 0
fi

# ---- 1. 系统检查 -------------------------------------------------------------
c_step "1/6 检查系统环境"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  c_info "系统：${PRETTY_NAME:-unknown}"
  case "${VERSION_ID:-}" in
    20.04|22.04|24.04) ;;
    *) c_warn "本脚本针对 Ubuntu 20.04 测试；当前为 ${VERSION_ID:-未知}，如遇问题请查看部署文档。" ;;
  esac
else
  c_warn "未找到 /etc/os-release，跳过系统版本检查。"
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "找不到 $PYTHON_BIN，请先安装：apt install -y python3 python3-venv python3-pip"

PY_VERSION="$($PYTHON_BIN -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"
PY_OK="$($PYTHON_BIN -c 'import sys;print(1 if sys.version_info>=(3,8) else 0)')"
c_info "Python：$PY_VERSION（$($PYTHON_BIN -c 'import sys;print(sys.executable)')）"
[[ "$PY_OK" == "1" ]] || die "需要 Python 3.8 及以上版本，当前为 $PY_VERSION"

if ! "$PYTHON_BIN" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  if [[ $SKIP_APT -eq 1 ]]; then
    die "缺少 python3-venv/ensurepip，请先执行：apt install -y python3-venv python3-pip"
  fi
  c_warn "缺少 python3-venv（venv 无 pip），尝试通过 apt 安装..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || c_warn "apt-get update 失败，继续尝试安装。"
  apt-get install -y python3-venv python3-pip || die "python3-venv 安装失败，请手动安装后重试。"
  if ! "$PYTHON_BIN" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    die "python3-venv 安装后仍不可用，请检查 apt 源后重试。"
  fi
fi
c_info "项目目录：$APP_DIR"

# ---- 2. 虚拟环境与依赖 -------------------------------------------------------
c_step "2/6 准备虚拟环境与依赖"
# 残缺虚拟环境自愈：如果 .venv 存在但没有 pip（通常是创建时系统缺
# python3-venv，Debian 会把环境目录建好却在 ensurepip 阶段失败），
# 直接重建，否则后续 pip 安装会以 “No module named pip” 静默失败。
if [[ -x "$VENV_DIR/bin/python" ]] && ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
  c_warn "已有虚拟环境缺少 pip，自动重建：$VENV_DIR"
  rm -rf "$VENV_DIR"
fi
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  c_info "创建虚拟环境：$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR" || die "创建虚拟环境失败。"
  if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    die "虚拟环境创建后仍没有 pip，请确认 python3-venv 已正确安装。"
  fi
else
  c_info "复用已有虚拟环境：$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

if [[ $SKIP_DEPS -eq 0 ]]; then
  "$VENV_PY" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 || c_warn "升级 pip 失败，继续使用现有版本。"
  INSTALLED=0
  for index in "${PIP_INDEXES[@]}"; do
    if [[ -n "$index" ]]; then
      c_info "安装依赖（镜像：$index）..."
      if "$VENV_PY" -m pip install -r "$APP_DIR/requirements.txt" -i "$index" --timeout 60; then
        INSTALLED=1; break
      fi
    else
      c_info "安装依赖（官方源 pypi.org）..."
      if "$VENV_PY" -m pip install -r "$APP_DIR/requirements.txt" --timeout 60; then
        INSTALLED=1; break
      fi
    fi
    c_warn "该源安装失败，尝试下一个源。"
  done
  if [[ $INSTALLED -eq 0 ]]; then
    c_warn "依赖安装失败：平台仍可运行，但只会使用启发式求解器。"
    c_warn "排查后可手动执行：$VENV_PIP install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
  fi
else
  c_info "已跳过依赖安装（--skip-deps）。"
fi

CP_SAT_READY=0
CP_SAT_PROBE="$("$VENV_PY" - 2>&1 <<'PY'
try:
    from ortools.sat.python import cp_model  # noqa: F401
except Exception as exc:
    print(f'{type(exc).__name__}: {exc}')
    raise SystemExit(1)
print('OK')
PY
)" || true
if [[ "$CP_SAT_PROBE" == "OK" ]]; then
  CP_SAT_READY=1
  ORTOOLS_VERSION="$("$VENV_PY" -c 'import ortools;print(getattr(ortools,"__version__","unknown"))' 2>/dev/null || echo unknown)"
  c_info "OR-Tools CP-SAT 可用：$ORTOOLS_VERSION"
else
  c_warn "未检测到可用的 OR-Tools：$CP_SAT_PROBE"
  c_warn "平台会用启发式求解器，功能正常；“仅 CP-SAT”选项会明确报错。"
  c_warn "如需修复，执行：$VENV_PIP install -r $APP_DIR/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
fi

# ---- 3. 运行配置 -------------------------------------------------------------
c_step "3/6 生成运行配置"
if [[ -f "$ENV_FILE" && $FORCE_ENV -eq 0 ]]; then
  c_info "沿用已有配置：$ENV_FILE（如需重置请加 --force-env）"
else
  if [[ -f "$ENV_FILE" ]]; then
    cp -a "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
  fi
  cp -a "$ENV_EXAMPLE" "$ENV_FILE"
  c_info "已生成配置：$ENV_FILE"
fi

set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  elif grep -qE "^#\s*${key}=" "$ENV_FILE"; then
    sed -i "s|^#\s*${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

if [[ -n "$PORT_OVERRIDE" ]]; then
  set_env PLATFORM_PORT "$PORT_OVERRIDE"
  c_info "监听端口：$PORT_OVERRIDE"
fi
if [[ -n "$HOST_OVERRIDE" ]]; then
  set_env PLATFORM_HOST "$HOST_OVERRIDE"
  c_info "监听地址：$HOST_OVERRIDE"
fi

PLATFORM_PORT_VALUE="$(grep -E '^PLATFORM_PORT=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
PLATFORM_HOST_VALUE="$(grep -E '^PLATFORM_HOST=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
PLATFORM_PORT_VALUE="${PLATFORM_PORT_VALUE:-8787}"
PLATFORM_HOST_VALUE="${PLATFORM_HOST_VALUE:-127.0.0.1}"

# ---- 4. 启动自检（部署前先验证代码与数据） -----------------------------------
c_step "4/6 启动自检"
if ! "$VENV_PY" "$APP_DIR/server.py" --check-only; then
  die "启动自检未通过，请根据上面的错误提示修复后重试。"
fi

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  c_warn "系统用户 $SERVICE_USER 不存在，改用 root 运行服务。"
  SERVICE_USER="root"
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER" 2>/dev/null || echo root)"
if [[ "$SERVICE_USER" != "root" ]]; then
  c_info "确保运行用户 $SERVICE_USER 可读项目文件..."
  chmod -R a+rX "$APP_DIR/app" "$APP_DIR/simulation" "$APP_DIR/data" "$APP_DIR/docs" 2>/dev/null || true
  chmod a+rx "$APP_DIR" 2>/dev/null || true
  chmod -R a+rX "$VENV_DIR" 2>/dev/null || true
fi

# 新版本把业务数据落在 SQLite（默认 data/platform.db），运行用户必须可写该目录，
# 否则服务能起来但所有接口 500（sqlite3.OperationalError: unable to open database file）。
DATA_DIR="${PLATFORM_DB_DIR:-$APP_DIR/data}"
mkdir -p "$DATA_DIR" 2>/dev/null || true
if [[ "$SERVICE_USER" != "root" ]]; then
  c_info "确保运行用户 $SERVICE_USER 可写数据目录 $DATA_DIR ..."
  chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR" 2>/dev/null \
    || c_warn "无法修改 $DATA_DIR 属主，请手动执行：chown -R $SERVICE_USER:$SERVICE_GROUP $DATA_DIR"
  chmod -R u+rwX "$DATA_DIR" 2>/dev/null || true
fi

# ---- 5. systemd 服务 ---------------------------------------------------------
c_step "5/6 注册 systemd 服务"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
if [[ ! -f "$UNIT_TEMPLATE" ]]; then
  die "找不到服务模板：$UNIT_TEMPLATE"
fi
sed -e "s|@APP_DIR@|$APP_DIR|g" \
    -e "s|@VENV_DIR@|$VENV_DIR|g" \
    -e "s|@ENV_FILE@|$ENV_FILE|g" \
    -e "s|@SERVICE_NAME@|$SERVICE_NAME|g" \
    -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
    -e "s|@SERVICE_GROUP@|$SERVICE_GROUP|g" \
    "$UNIT_TEMPLATE" > "$UNIT_PATH"
c_info "已写入：$UNIT_PATH（运行用户：$SERVICE_USER，端口：$PLATFORM_HOST_VALUE:$PLATFORM_PORT_VALUE）"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || c_warn "设置开机自启失败，可稍后手动执行 systemctl enable $SERVICE_NAME"

health_check() {
  "$VENV_PY" - "$PLATFORM_PORT_VALUE" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
url = f'http://127.0.0.1:{port}/api/health'
try:
    with urllib.request.urlopen(url, timeout=5) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    print(f"[信息] 健康检查通过：status={payload.get('status')} 数据就绪={payload.get('data', {}).get('ready')}")
    sys.exit(0)
except Exception as exc:
    print(f'[错误] 健康检查失败：{exc}', file=sys.stderr)
    sys.exit(1)
PY
}

if [[ $DO_START -eq 1 ]]; then
  systemctl restart "$SERVICE_NAME"
  c_info "服务已启动，等待就绪..."
  READY=0
  for _ in $(seq 1 15); do
    sleep 1
    if health_check >/dev/null 2>&1; then
      READY=1
      break
    fi
  done
  if [[ $READY -eq 1 ]]; then
    health_check || true
  else
    c_err "服务在 15 秒内未通过健康检查，最近日志如下："
    journalctl -u "$SERVICE_NAME" -n 25 --no-pager || true
    exit 4
  fi
else
  c_info "已跳过启动（--no-start）。"
fi

# ---- 6. 可选的 Nginx 站点 ----------------------------------------------------
c_step "6/6 Nginx 反向代理"
if [[ $WITH_NGINX -eq 1 ]]; then
  [[ -n "$NGINX_DOMAIN" ]] || die "--with-nginx 需要跟域名，例如：--with-nginx demo.example.com"
  BAOTA_VHOST_DIR="/www/server/panel/vhost/nginx"
  if [[ -d "$BAOTA_VHOST_DIR" ]]; then
    TARGET="$BAOTA_VHOST_DIR/${NGINX_DOMAIN}.conf"
    [[ -f "$TARGET" ]] && cp -a "$TARGET" "$TARGET.bak.$(date +%Y%m%d%H%M%S)"
    sed -e "s|__DOMAIN__|$NGINX_DOMAIN|g" \
        -e "s|__PORT__|$PLATFORM_PORT_VALUE|g" \
        -e "s|__APP_DIR__|$APP_DIR|g" \
        "$TEMPLATE_DIR/nginx-baota-site.conf" > "$TARGET"
    c_info "已写入宝塔站点配置：$TARGET"
    NGINX_BIN="/www/server/nginx/sbin/nginx"
    if [[ -x "$NGINX_BIN" ]] && "$NGINX_BIN" -t 2>/dev/null; then
      "$NGINX_BIN" -s reload && c_info "宝塔 Nginx 已重载。"
    else
      c_warn "Nginx 配置校验未通过或未找到宝塔 Nginx，请到面板【网站 → 设置 → 配置文件】检查。"
    fi
  elif [[ -d /etc/nginx ]]; then
    TARGET="/etc/nginx/conf.d/${NGINX_DOMAIN}.conf"
    [[ -f "$TARGET" ]] && cp -a "$TARGET" "$TARGET.bak.$(date +%Y%m%d%H%M%S)"
    sed -e "s|__DOMAIN__|$NGINX_DOMAIN|g" \
        -e "s|__PORT__|$PLATFORM_PORT_VALUE|g" \
        "$TEMPLATE_DIR/nginx-standalone.conf" > "$TARGET"
    c_info "已写入 Nginx 配置：$TARGET"
    if nginx -t 2>/dev/null; then
      systemctl reload nginx && c_info "Nginx 已重载。"
    else
      c_warn "nginx -t 未通过，请检查 $TARGET"
    fi
  else
    c_warn "未找到 Nginx（宝塔或系统 nginx 都没有），已跳过。装好面板后按文档添加反向代理即可。"
  fi
else
  c_info "未指定 --with-nginx，跳过。"
  c_info "请在宝塔面板：网站 → 添加站点 → 设置 → 反向代理，目标 http://127.0.0.1:${PLATFORM_PORT_VALUE}"
  c_info "务必删除该站点配置里默认的 js/css 静态缓存 location（详见部署文档第 3 节）。"
fi

# ---- 完成 --------------------------------------------------------------------
cat <<EOF

=============================================================================
 部署完成
=============================================================================
 本机访问      : http://127.0.0.1:${PLATFORM_PORT_VALUE}
 健康检查      : http://127.0.0.1:${PLATFORM_PORT_VALUE}/api/health
 监听地址      : ${PLATFORM_HOST_VALUE}:${PLATFORM_PORT_VALUE}（由 Nginx 反向代理对外）
 OR-Tools      : $([[ $CP_SAT_READY -eq 1 ]] && echo '已安装（可用“仅 CP-SAT”演示）' || echo '未安装（使用启发式，功能正常）')
 服务名        : ${SERVICE_NAME}
 配置文件      : ${ENV_FILE}
 运行日志      : journalctl -u ${SERVICE_NAME} -f

 常用命令：
   systemctl status ${SERVICE_NAME}
   systemctl restart ${SERVICE_NAME}
   systemctl stop ${SERVICE_NAME}
   bash deploy/check.sh          # 一键体检
   bash deploy/install.sh --uninstall   # 卸载服务（保留文件）

 还需要在面板/云厂商侧完成：
   1. 宝塔【网站】添加站点，域名解析到本机公网 IP；
   2. 站点【反向代理】指向 http://127.0.0.1:${PLATFORM_PORT_VALUE}；
      （若使用 --with-nginx，配置已写好，但仍建议在面板里确认站点存在）
   3. 面板【SSL】申请证书并开启 HTTPS；云服务器安全组放行 80/443。
   4. 不要对公网开放 ${PLATFORM_PORT_VALUE} 端口。

 详细说明见：docs/部署到Ubuntu宝塔面板.md
=============================================================================
EOF
