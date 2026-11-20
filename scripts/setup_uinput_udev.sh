#!/usr/bin/env bash
set -euo pipefail

RULE_PATH="/etc/udev/rules.d/99-keyless-uinput.rules"
RULE_CONTENT='KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", TAG+="uaccess"'

echo "Installing udev rule to ${RULE_PATH}..."
echo "${RULE_CONTENT}" | sudo tee "${RULE_PATH}" >/dev/null

if getent group input >/dev/null; then
    if ! id -nG "${USER}" | tr ' ' '\n' | grep -qx input; then
        echo "Adding ${USER} to 'input' group (re-login may be needed for group membership; uaccess takes effect immediately)..."
        sudo usermod -aG input "${USER}"
    fi
fi

echo "Reloading udev rules and triggering /dev/uinput..."
sudo udevadm control --reload-rules
sudo udevadm trigger /dev/uinput

ls -l /dev/uinput
if [[ -w /dev/uinput ]]; then
    echo "Ready: /dev/uinput is writable by ${USER} via uaccess ACL."
else
    echo "Warning: /dev/uinput is still not writable by ${USER}. Try logging out and back in."
fi
