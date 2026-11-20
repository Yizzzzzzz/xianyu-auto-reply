#!/bin/bash
# 如果是 root 执行，自动以 xky 用户身份运行
if [ "$(id -u)" -eq 0 ]; then
    exec su - xky -c "bash \"$0\" $*"
fi

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
unset DBUS_SESSION_BUS_ADDRESS

systemctl --user restart xianyu-backend xianyu-scheduler xianyu-websocket xianyu-frontend xianyu-vnc xianyu-adapter
echo '服务已重启！'
systemctl --user status xianyu-backend xianyu-scheduler xianyu-websocket xianyu-frontend xianyu-vnc xianyu-adapter --no-pager
