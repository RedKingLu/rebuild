#!/usr/bin/env bash
# 运行 rebuild 的 Node 版 Playwright e2e 脚本。
#
# 用法：  ./e2e/run-e2e.sh e2e/r18_1_g3_l5_gate.mjs [脚本参数...]
#
# 为什么需要包装：WSL / 精简 Linux 上 Playwright 下载的 Chromium 缺 4~5 个系统库
# （libnspr4 / libnss3 / libnssutil3 / libsmime3 / libasound.so.2）。
#
# 两种解法：
#   ① 系统级（推荐，一次到位，之后本脚本的 LD_LIBRARY_PATH 分支自动失效不生效）：
#        sudo apt-get install -y libnss3 libasound2t64
#   ② 免 sudo（本脚本支持）：把 deb 解包到 ~/.local/playwright-deps，
#      本脚本自动探测并注入 LD_LIBRARY_PATH：
#        cd /tmp && apt-get download libnss3 libnspr4 libasound2t64
#        for d in *.deb; do dpkg -x "$d" ~/.local/playwright-deps; done
#
# 浏览器本身若未装（或 CDN 被拦），用国内镜像装：
#   PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
#     npx playwright install chromium
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "用法: $0 <e2e 脚本.mjs> [参数...]" >&2
  exit 2
fi

LOCAL_DEPS="$HOME/.local/playwright-deps/usr/lib/x86_64-linux-gnu"
if [ -f "$LOCAL_DEPS/libnspr4.so" ]; then
  # 追加而非覆盖，避免破坏调用方已有的 LD_LIBRARY_PATH
  export LD_LIBRARY_PATH="$LOCAL_DEPS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  echo "[run-e2e] 已注入本地 Playwright 依赖库: $LOCAL_DEPS" >&2
fi

exec node "$@"
