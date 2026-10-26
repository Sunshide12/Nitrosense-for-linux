#!/usr/bin/env bash
# Desinstala NitroSense para Linux.
set -uo pipefail

[[ $EUID -eq 0 ]] || { echo "Ejecuta con sudo: sudo ./uninstall.sh" >&2; exit 1; }

modprobe -r nitrosense 2>/dev/null
dkms remove -m nitrosense --all 2>/dev/null
rm -rf /usr/src/nitrosense-* /usr/share/nitrosense /usr/libexec/nitrosense
rm -f /usr/bin/nitrosense /usr/bin/nitrosense-ctl \
      /usr/share/applications/io.github.nitrosense.Linux.desktop \
      /usr/share/icons/hicolor/scalable/apps/io.github.nitrosense.Linux.svg \
      /etc/udev/rules.d/70-nitrosense.rules \
      /etc/systemd/system/nitrosense-restore.service \
      /usr/lib/systemd/system-sleep/nitrosense \
      /usr/lib/systemd/user/nitrosense-lights.service \
      /etc/modules-load.d/nitrosense.conf
# the dynamic-lighting user service is enabled per user: drop the autostart link and stop the loop
if [[ -n "${SUDO_USER:-}" ]]; then
    rm -f "$(getent passwd "$SUDO_USER" | cut -d: -f6)/.config/systemd/user/default.target.wants/nitrosense-lights.service"
fi
pkill -f 'nitrosense-ctl light run' 2>/dev/null
systemctl daemon-reload
udevadm control --reload

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf /var/lib/nitrosense
    groupdel nitrosense 2>/dev/null
fi
echo "NitroSense desinstalado. (Usa --purge para borrar también los ajustes y el grupo.)"
