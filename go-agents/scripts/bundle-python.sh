#!/usr/bin/env bash
# bundle-python.sh — 用 python-build-standalone 产出自包含 Python 分发，
# 预装 GenericAgent 的第三方依赖，供 Go 重写版的子进程调用点使用：
#   code_run / tmwebdriver_bridge.py / reflect check()/on_done() / mykey 加载。
#
# 复刻自 Galley scripts/bundle-python.sh（Galley 是 GA 的 Tauri 外壳，依赖集一致）。
# 改动：REPO_ROOT 指向 go-agents；输出 python-bundle/；verify 仅查依赖导入；
#       新增 linux-x64。
#
# Usage: ./scripts/bundle-python.sh {mac-x64|mac-arm64|win-x64|linux-x64}
# 输出：go-agents/python-bundle/python/  （.gitignored，每台机器/CI 重新生成）

set -euo pipefail

PBS_RELEASE="20260510"
PBS_PYTHON_VERSION="3.11.15"

# GenericAgent 第三方依赖（与 GA 上游一致，锁版本可复现）。
GA_DEPS=(
  "requests==2.34.2"
  "beautifulsoup4==4.14.3"
  "bottle==0.13.4"
  "simple-websocket-server==0.4.4"
  "aiohttp==3.13.5"
  "qrcode[pil]==8.2"
  "pycryptodome==3.23.0"
  "python-dotenv==1.2.1"
  "lark-oapi==1.6.8"
)

ARCH="${1:-}"
if [[ -z "$ARCH" ]]; then
  echo "Usage: $0 {mac-x64|mac-arm64|win-x64|linux-x64}" >&2
  exit 1
fi

case "$ARCH" in
  mac-x64)    PBS_TRIPLE="x86_64-apple-darwin" ;;
  mac-arm64)  PBS_TRIPLE="aarch64-apple-darwin" ;;
  win-x64)    PBS_TRIPLE="x86_64-pc-windows-msvc" ;;
  linux-x64)  PBS_TRIPLE="x86_64-unknown-linux-gnu" ;;
  *) echo "Unknown arch: $ARCH" >&2; exit 1 ;;
esac

PBS_FILE="cpython-${PBS_PYTHON_VERSION}+${PBS_RELEASE}-${PBS_TRIPLE}-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${REPO_ROOT}/python-bundle"
CACHE_DIR="${REPO_ROOT}/.cache/pbs"

echo "[bundle-python] arch=${ARCH} triple=${PBS_TRIPLE}"
echo "[bundle-python] output: ${OUT_DIR}/python"

mkdir -p "$CACHE_DIR"
if [[ ! -f "${CACHE_DIR}/${PBS_FILE}" ]]; then
  echo "[bundle-python] downloading PBS..."
  curl -sL -o "${CACHE_DIR}/${PBS_FILE}" "$PBS_URL"
else
  echo "[bundle-python] PBS cached"
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
tar -xzf "${CACHE_DIR}/${PBS_FILE}" -C "$OUT_DIR"

PYTHON_DIR="${OUT_DIR}/python"
if [[ "$ARCH" == "win-x64" ]]; then
  PYTHON_BIN="${PYTHON_DIR}/python.exe"
  STDLIB="${PYTHON_DIR}/Lib"
else
  PYTHON_BIN="${PYTHON_DIR}/bin/python3"
  STDLIB="${PYTHON_DIR}/lib/python3.11"
fi

echo "[bundle-python] stripping non-essential stdlib..."
rm -rf "${STDLIB}/test" \
       "${STDLIB}/tkinter" \
       "${STDLIB}/idlelib" \
       "${STDLIB}/turtledemo" \
       "${STDLIB}/ensurepip"

if [[ "$ARCH" != "win-x64" ]]; then
  find "${STDLIB}/lib-dynload" -name "_tkinter*" -delete 2>/dev/null || true
  rm -f "${PYTHON_DIR}/bin/2to3"* \
        "${PYTHON_DIR}/bin/idle"* \
        "${PYTHON_DIR}/bin/pydoc"*
fi

echo "[bundle-python] installing GA deps to bundle's site-packages..."
"$PYTHON_BIN" -m pip install \
  --no-warn-script-location \
  --no-compile \
  --disable-pip-version-check \
  --quiet \
  "${GA_DEPS[@]}"

echo "[bundle-python] verifying bundle deps..."
"$PYTHON_BIN" -c '
import aiohttp, requests, bs4, bottle, dotenv
import simple_websocket_server
import Crypto
import lark_oapi
import qrcode
print("  deps OK")
'

BUNDLE_SIZE=$(du -sh "$PYTHON_DIR" | awk '{print $1}')
echo "[bundle-python] done. bundle size: ${BUNDLE_SIZE}"
echo "[bundle-python] python bin: ${PYTHON_BIN}"
echo "[bundle-python] Go 解析器优先级: GA_PYTHON → <exe_dir>/python → <root>/python-bundle/python → 系统 python3"
