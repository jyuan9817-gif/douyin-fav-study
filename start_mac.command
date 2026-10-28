#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "请先双击 install_mac.command 安装。"; read -p "按回车退出"; exit 1
fi
.venv/bin/python -m app
