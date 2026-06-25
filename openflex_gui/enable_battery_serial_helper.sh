#!/bin/bash
# OpenFlex battery serial helper
# Uses pkexec for a single elevation step, then grants temporary access to
# detected USB serial devices so the battery monitor can probe them.

set -euo pipefail

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
echo "OpenFlex battery serial permission helper"
echo "=================================================="

shopt -s nullglob
ports=(/dev/ttyACM* /dev/ttyUSB*)

if [[ ${#ports[@]} -eq 0 ]]; then
    echo "[ERR] No ttyACM/ttyUSB serial devices found"
    exit 1
fi

success=0
total=0

for port in "${ports[@]}"; do
    if [[ ! -e "$port" ]]; then
        continue
    fi

    total=$((total + 1))
    if chmod a+rw "$port"; then
        echo "[ OK ] $port readable/writable"
        success=$((success + 1))
    else
        echo "[ERR] Failed to chmod $port"
    fi
done

echo "=================================================="
echo "Battery serial permission complete: ${success}/${total}"
echo "=================================================="

if [[ ${success} -gt 0 ]]; then
    exit 0
fi

exit 1
