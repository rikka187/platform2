#!/usr/bin/env bash
# =============================================================================
# 银犁物流仓配一体化协同平台 —— 前台运行脚本（调试用，不用 systemd）
#
#   bash deploy/run.sh                 # 用 deploy/platform.env（若存在）
#   PLATFORM_PORT=9000 bash deploy/run.sh
#
# 生产环境请用 systemd：sudo bash deploy/install.sh
# =============================================================================
set -Eeuo pipefail

if grep -q $'\r' "$0" 2>/dev/null; then
  echo "错误：检测到 Windows 换行符(CRLF)。请先执行： sed -i 's/\\r\$//' deploy/*.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$SCRIPT_DIR/platform.env" ]]; then
  # shellcheck disable=SC1091
  set -a; . "$SCRIPT_DIR/platform.env"; set +a
fi

if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  PY="$APP_DIR/.venv/bin/python"
else
  PY="${PYTHON_BIN:-python3}"
fi

echo "[信息] 使用解释器：$PY"
exec "$PY" "$APP_DIR/server.py" \
  --host "${PLATFORM_HOST:-127.0.0.1}" \
  --port "${PLATFORM_PORT:-8787}" \
  "$@"
