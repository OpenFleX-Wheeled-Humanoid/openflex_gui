#!/bin/bash
# OpenFlex VR CAN helper
# Uses pkexec for a single elevation step, then configures all detected CAN
# interfaces needed by the integrated robot.

set -euo pipefail

declare -A ROBOT_CAN_CONFIG=(
    [can0]=1000000
    [can1]=1000000
    [can2]=1000000
    [can3]=1000000
    [can4]=1000000
    [can5]=1000000
)

bring_up_can() {
    local iface="$1"
    local bitrate="$2"

    ip link set "$iface" down 2>/dev/null || true
    ip link set "$iface" type can bitrate "$bitrate" loopback off
    ip link set "$iface" up
    echo "[ OK ] $iface ${bitrate}bps UP"
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
echo "OpenFlex VR enabling CAN interfaces"
echo "=================================================="

success=0
total=0
found=0

for iface in $(printf '%s\n' "${!ROBOT_CAN_CONFIG[@]}" | sort); do
    bitrate="${ROBOT_CAN_CONFIG[$iface]}"
    if ! ip link show "$iface" &>/dev/null; then
        echo "[SKIP] $iface does not exist"
        continue
    fi

    total=$((total + 1))
    found=$((found + 1))
    if bring_up_can "$iface" "$bitrate"; then
        success=$((success + 1))
    fi
done

echo "=================================================="
echo "CAN enable complete: ${success}/${total}"
echo "=================================================="

if [[ ${found} -eq 0 ]]; then
    exit 1
fi

if [[ ${success} -eq ${total} ]]; then
    exit 0
fi

exit 1
