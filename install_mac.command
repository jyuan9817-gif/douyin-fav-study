#!/bin/bash
cd "$(dirname "$0")"
echo "=================================================="
echo "  收藏学习台 · 安装（只需要运行一次）"
echo "=================================================="

# 1. 找 Python：只用 3.10–3.12（3.13 及以上有些组件还没有安装包）
pyver() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }
ok_ver() { case "$1" in 3.10|3.11|3.12) return 0;; *) return 1;; esac; }
PY=""; SEEN=""
for c in python3.12 python3.11 python3.10 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
         /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
         /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
         python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$(pyver "$c")
    [ -n "$v" ] && SEEN="$SEEN $v"
    if ok_ver "$v"; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo ""
  if [ -n "$SEEN" ]; then
    echo "❌ 你电脑上的 Python 版本是:$SEEN，太新或太旧，有些组件装不上。"
  else
    echo "❌ 没找到 Python。"
  fi
  echo "   请到 https://www.python.org/downloads/ 往下翻，下载安装 Python 3.12（不用卸载已有的版本），"
  echo "   装好后重新双击本脚本。"
  read -p "按回车退出"; exit 1
fi
echo "使用 Python $(pyver "$PY")（$PY）"

# 2. 独立运行环境：版本不对或坏了就重建
if [ -x .venv/bin/python ] && ! ok_ver "$(pyver .venv/bin/python)"; then
  echo "旧的运行环境版本不对，重新创建……"; rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  echo "[1/3] 创建独立运行环境……"
  "$PY" -m venv .venv || { echo "❌ 创建运行环境失败"; read -p "按回车退出"; exit 1; }
else
  echo "[1/3] 运行环境已存在，继续安装组件……"
fi
VPY=.venv/bin/python
$VPY -m pip install -q -U pip --timeout 30 >/dev/null 2>&1 || $VPY -m pip install -q -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple >/dev/null 2>&1

# 3. 逐个安装组件：一个失败不影响其他
echo "[2/3] 安装组件（需要几分钟）……"
pipi() {  # 先官方源，失败再用清华镜像
  $VPY -m pip install -q "$@" --timeout 30 && return 0
  echo "   官方源失败，改用清华镜像重试……"
  $VPY -m pip install -q "$@" -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30
}
OK=(); BAD=()
step() {  # step 名称 包...
  local name="$1"; shift
  echo " · $name"
  if pipi "$@"; then OK+=("$name"); else BAD+=("$name"); fi
}
step "读取抖音（playwright）" "playwright>=1.45"
step "语音转文字（faster-whisper）" "faster-whisper>=1.0.3"
if [ "$(uname -m)" = "arm64" ]; then
  step "苹果芯片加速转写（mlx-whisper）" "mlx-whisper>=0.4.2"
fi
step "图片文字识别（ocrmac）" "ocrmac>=1.0.0"

echo "[3/3] 检查浏览器……"
if $VPY -c "import playwright" 2>/dev/null; then
  if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "/Applications/Microsoft Edge.app" ]; then
    echo "没找到 Chrome / Edge，下载内置浏览器……"
    $VPY -m playwright install chromium || BAD+=("内置浏览器（chromium）")
  fi
fi
chmod +x start_mac.command 2>/dev/null

echo ""
echo "=================================================="
for x in "${OK[@]}"; do echo "  ✅ $x"; done
for x in "${BAD[@]}"; do echo "  ❌ $x"; done
echo "=================================================="
if [ ${#BAD[@]} -eq 0 ]; then
  echo "✅ 安装完成！以后双击 start_mac.command 启动。"
else
  echo "⚠️  有 ${#BAD[@]} 个组件没装上（多半是网络问题）。"
  echo "   直接再双击一次本脚本就会只补装缺的部分。"
  echo "   如果还是失败，把这个窗口截图发给作者。"
  if $VPY -c "import playwright" 2>/dev/null; then
    echo "   程序可以先启动用，缺的功能会在网页里提示。"
  fi
fi
read -p "按回车关闭"
