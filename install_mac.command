#!/bin/bash
cd "$(dirname "$0")"
echo "=================================================="
echo "  收藏学习台 · 安装（只需要运行一次）"
echo "=================================================="
PY=""
for c in python3.12 python3.11 python3.13 python3; do
  if command -v $c >/dev/null 2>&1; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
  echo "❌ 没找到 Python。请先到 https://www.python.org/downloads/ 下载安装 Python 3.11 或 3.12，再运行本脚本。"
  read -p "按回车退出"; exit 1
fi
echo "使用 $($PY --version)"
echo "[1/3] 创建独立运行环境……"
$PY -m venv .venv || { echo "❌ 创建失败"; read -p "按回车退出"; exit 1; }
echo "[2/3] 安装依赖（需要几分钟）……"
.venv/bin/python -m pip install -q -U pip || true
if ! .venv/bin/python -m pip install -r requirements.txt --timeout 30; then
  echo "官方源失败，改用清华镜像……"
  .venv/bin/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple || { echo "❌ 安装失败"; read -p "按回车退出"; exit 1; }
fi
echo "[3/3] 检查浏览器……"
if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "/Applications/Microsoft Edge.app" ]; then
  echo "没找到 Chrome / Edge，下载内置浏览器……"
  .venv/bin/python -m playwright install chromium
fi
chmod +x start_mac.command
echo ""
echo "✅ 安装完成！以后双击 start_mac.command 启动。"
read -p "按回车关闭"
