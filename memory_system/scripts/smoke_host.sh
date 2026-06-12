#!/usr/bin/env bash
# 端到端 smoke（宿主版）：通过 docker exec 在 memory-allinone 内执行 Python smoke。
# 用法：./smoke_host.sh
# 退出码：0=全部 probe 通过；非 0=有 FAIL。
#
# 背景：宿主 curl 容器 8010 拒连（沙箱 netns 假同源），所以包一层 docker exec。

set -euo pipefail

CONTAINER="${MEMORY_CONTAINER:-memory-allinone}"
SCRIPT_LOCAL="$(dirname "$0")/_smoke_payload.py"
SCRIPT_REMOTE="/tmp/smoke_in_container.py"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "ERROR: container '${CONTAINER}' not running" >&2
  exit 2
fi

if [[ ! -f "$SCRIPT_LOCAL" ]]; then
  echo "ERROR: payload missing: $SCRIPT_LOCAL" >&2
  exit 2
fi

docker cp "$SCRIPT_LOCAL" "${CONTAINER}:${SCRIPT_REMOTE}" >/dev/null
exec docker exec "${CONTAINER}" python3 "${SCRIPT_REMOTE}"
