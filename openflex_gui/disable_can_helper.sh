#!/bin/bash
# OpenFlex VR CAN helper
# Uses pkexec for a single elevation step, then disables all detected CAN
# interfaces used by the integrated robot.

set -euo pipefail

ROBOT_CAN_IFACES=(can0 can1 can2 can3 can4 can5)

bring_down_can() {
    local iface="$1"

    ip link set "$iface" down
    echo "[ OK ] $iface DOWN"
}

if [[ ${EUID} -ne 0 ]]; then
    export DISPLAY="${DISPLAY:-:0}"
    export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

    exec pkexec env \
        DISPLAY="$DISPLAY" \
        XAUTHORITY="$XAUTHORITY" \
        HOME="$HOME" \
        USER="$USER" \
        PYTHONIOENCODING=utf-8 \
        bash "$0" "$@"
fi

echo "=================================================="
echo "OpenFlex VR disabling CAN interfaces"
echo "=================================================="

success=0
total=0
found=0

for iface in "${ROBOT_CAN_IFACES[@]}"; do
    if ! ip link show "$iface" &>/dev/null; then
        echo "[SKIP] $iface does not exist"
        continue
    fi

    total=$((total + 1))
    found=$((found + 1))
    if bring_down_can "$iface"; then
        success=$((success + 1))
    fi
done

echo "=================================================="
echo "CAN disable complete: ${success}/${total}"
echo "=================================================="

if [[ ${found} -eq 0 ]]; then
    exit 1
fi

if [[ ${success} -eq ${total} ]]; then
    exit 0
fi

exit 1
