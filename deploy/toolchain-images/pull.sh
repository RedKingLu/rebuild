#!/usr/bin/env bash
# R19-1 G1：工具链构建镜像【预热】脚本。
#
# 为什么要预热而不是运行期拉取：
#   镜像拉取写宿主镜像库 + 触及外部 registry ⇒ 属 L4 系统级操作（02-术语表 第七部分）。
#   而 P5 对 L4/L5 命令一律置 needs_user_input（p5_command_service），若拉取发生在 P5 循环内，
#   验收锚点 R19-1-03 会落成 needs_user_input（要求是「真实 validation_failed」）。
#   ⇒ 把这个 L4 动作显式前置到 P5 之外：人在场、审计在案、一次性。
#   这不是绕过 Gate —— L4 照样要人确认，只是不把「等用户批准拉镜像」
#   混淆成「构建结论待用户输入」。（Q-R19-1-2 裁决 C）
#
# 用法：
#   bash deploy/toolchain-images/pull.sh                       # 拉取映射表中全部镜像
#   bash deploy/toolchain-images/pull.sh mcr.microsoft.com/dotnet/sdk:8.0   # 只拉指定镜像
#   bash deploy/toolchain-images/pull.sh --list                # 只列出映射表内容，不拉取
#   bash deploy/toolchain-images/pull.sh --save <dir>          # 离线包：拉取后 docker save
#
# 安全护栏：
#   - 只允许映射表 `registries_allowed` 内的 host；
#   - 只允许映射表中【出现过的】镜像引用（不接受任意镜像名）；
#   - 拉取后打印 RepoDigest（滚动 tag 会漂移，digest 才可复现，Q-R19-1-6）。
#
# ❌ 本脚本不碰 /var/run/docker.sock 挂载、不改 sock 权限、不停宿主 docker。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONFIG="${REPO_ROOT}/backend/app/config/toolchain_images.yaml"

if [[ ! -f "${CONFIG}" ]]; then
  echo "[toolchain] 映射表不存在：${CONFIG}" >&2
  exit 1
fi

PY="python3"
if [[ -x "${REPO_ROOT}/backend/.venv/bin/python" ]]; then
  PY="${REPO_ROOT}/backend/.venv/bin/python"
fi

# 从映射表读出：允许的 registry host 清单 + 全部镜像引用（唯一事实源 = yaml）
read_config() {
  "${PY}" - "${CONFIG}" "$1" <<'PYEOF'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
what = sys.argv[2]
if what == "registries":
    print("\n".join(cfg.get("registries_allowed") or []))
elif what == "images":
    out = []
    for tc in (cfg.get("toolchains") or {}).values():
        for img in (tc.get("target_framework_map") or {}).values():
            if img and img not in out:
                out.append(img)
        d = tc.get("default_image")
        if d and d not in out:
            out.append(d)
    print("\n".join(out))
PYEOF
}

mapfile -t ALLOWED_REGISTRIES < <(read_config registries)
mapfile -t KNOWN_IMAGES < <(read_config images)

if [[ "${1:-}" == "--list" ]]; then
  echo "[toolchain] 允许的 registry：${ALLOWED_REGISTRIES[*]}"
  echo "[toolchain] 映射表内镜像："
  printf '  %s\n' "${KNOWN_IMAGES[@]}"
  exit 0
fi

SAVE_DIR=""
if [[ "${1:-}" == "--save" ]]; then
  SAVE_DIR="${2:?--save 需要目标目录}"
  shift 2
fi

TARGETS=()
if [[ $# -gt 0 ]]; then
  TARGETS=("$@")
else
  TARGETS=("${KNOWN_IMAGES[@]}")
fi

is_known() {
  local needle="$1"
  for img in "${KNOWN_IMAGES[@]}"; do
    [[ "${img}" == "${needle}" ]] && return 0
  done
  return 1
}

host_allowed() {
  local host="${1%%/*}"
  for r in "${ALLOWED_REGISTRIES[@]}"; do
    [[ "${host}" == "${r}" ]] && return 0
  done
  return 1
}

if ! docker version >/dev/null 2>&1; then
  echo "[toolchain] Docker 守护不可达 —— 无法预热镜像。" >&2
  echo "[toolchain] 平台侧会诚实标 evidence_gap（不伪造 available）。" >&2
  exit 2
fi

for IMAGE in "${TARGETS[@]}"; do
  if ! is_known "${IMAGE}"; then
    echo "[toolchain] 拒绝：${IMAGE} 不在映射表内（只允许 toolchain_images.yaml 中出现过的引用）" >&2
    exit 3
  fi
  if ! host_allowed "${IMAGE}"; then
    echo "[toolchain] 拒绝：${IMAGE} 的 registry 不在白名单 ${ALLOWED_REGISTRIES[*]}" >&2
    exit 3
  fi

  echo "[toolchain] ⚠ L4 系统级操作：即将从外部 registry 拉取镜像（写宿主镜像库）"
  echo "[toolchain]   镜像：${IMAGE}"
  docker pull "${IMAGE}"

  DIGEST="$(docker image inspect "${IMAGE}" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}(无 RepoDigest){{end}}')"
  SIZE="$(docker image inspect "${IMAGE}" --format '{{.Size}}')"
  echo "[toolchain] 已就位：${IMAGE}"
  echo "[toolchain]   digest：${DIGEST}"
  echo "[toolchain]   size  ：${SIZE} bytes"

  if [[ -n "${SAVE_DIR}" ]]; then
    mkdir -p "${SAVE_DIR}"
    OUT="${SAVE_DIR}/$(echo "${IMAGE}" | tr '/:' '__').tar"
    echo "[toolchain] 离线包导出：${OUT}"
    docker save -o "${OUT}" "${IMAGE}"
  fi
done

echo "[toolchain] 完成。离线导入：docker load -i <上述 tar>（见本目录 README.md）"
