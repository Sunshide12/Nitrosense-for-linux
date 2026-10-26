#!/usr/bin/env bash
# NitroSense para Linux: instalador para Fedora (Workstation / spins con dnf).
set -euo pipefail

PKG_VERSION=1.0.0   # no llamarla VERSION: /etc/os-release la sobrescribe
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

say()  { printf '\033[1;31m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mAviso:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "ejecuta el instalador con sudo: sudo ./install.sh"

# --- comprobaciones -------------------------------------------------------
. /etc/os-release
if [[ "${ID:-}" != "fedora" && "${ID_LIKE:-}" != *fedora* ]]; then
    warn "este instalador está pensado para Fedora (detectado: ${PRETTY_NAME:-desconocido})"
fi
if [[ -e /run/ostree-booted ]]; then
    die "Fedora Atomic (Silverblue/Kinoite) no está soportado por este instalador: /usr es de solo lectura"
fi

vendor="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)"
product="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"
say "Equipo detectado: $vendor $product"
if [[ "$vendor" != Acer* ]]; then
    [[ $FORCE -eq 1 ]] || die "no es un portátil Acer. Usa --force si sabes lo que haces."
elif [[ "$product" != *AN515-57* ]]; then
    warn "el driver se reconstruyó a partir de un Nitro AN515-57; en '$product' puede comportarse distinto"
    [[ $FORCE -eq 1 ]] || die "usa ./install.sh --force para instalar igualmente"
fi
if ! compgen -G "/sys/bus/wmi/devices/7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56*" >/dev/null; then
    warn "la BIOS no expone la interfaz WMI de juego (7A4DDFE7-...); el driver no se activará"
fi

# --- dependencias -----------------------------------------------------------
say "Instalando dependencias (dkms, kernel-devel, GTK4, libadwaita)…"
KVER="$(uname -r)"
dnf install -y dkms gcc make mokutil python3 python3-gobject python3-cairo gobject-introspection gtk4 libadwaita \
    "kernel-devel-$KVER" 2>/dev/null \
  || dnf install -y dkms gcc make mokutil python3 python3-gobject python3-cairo gobject-introspection gtk4 libadwaita kernel-devel
[[ -d "/lib/modules/$KVER/build" ]] || \
    warn "no hay cabeceras para el kernel en ejecución ($KVER). Actualiza y reinicia: sudo dnf upgrade --refresh"

# --- módulo del kernel (DKMS) -------------------------------------------------
say "Compilando el módulo del kernel con DKMS…"
# Una versión anterior de este script registró una versión inválida ("44 (Workstation Edition)",
# tomada de /etc/os-release). Esa entrada rota hace fallar a `dkms remove`: se borra primero.
for stale in /var/lib/dkms/nitrosense/*[!0-9.]* /usr/src/nitrosense-*[!0-9.]*; do
    if [[ -e $stale || -L $stale ]]; then
        warn "eliminando entrada DKMS antigua: ${stale##*/}"
        rm -rf -- "$stale"
    fi
done
dkms remove -m nitrosense -v "$PKG_VERSION" --all >/dev/null 2>&1 || true
# si aún figura registrada, se borra su árbol a mano para que `dkms add` no se niegue
if dkms status -m nitrosense -v "$PKG_VERSION" 2>/dev/null | grep -q nitrosense; then
    rm -rf -- "/var/lib/dkms/nitrosense/$PKG_VERSION"
fi
rm -rf "/usr/src/nitrosense-$PKG_VERSION"
install -d "/usr/src/nitrosense-$PKG_VERSION"
install -m 0644 "$SRC"/kernel/{nitrosense.c,Makefile,dkms.conf} "/usr/src/nitrosense-$PKG_VERSION/"
dkms add -m nitrosense -v "$PKG_VERSION"
dkms build -m nitrosense -v "$PKG_VERSION"
dkms install -m nitrosense -v "$PKG_VERSION" --force
echo nitrosense > /etc/modules-load.d/nitrosense.conf

# --- Secure Boot ----------------------------------------------------------------
NEED_MOK=0
if mokutil --sb-state 2>/dev/null | grep -qi "enabled"; then
    MOK_PUB=""
    for f in /var/lib/dkms/mok.pub /etc/dkms/mok.pub; do [[ -f $f ]] && MOK_PUB=$f && break; done
    if [[ -n "$MOK_PUB" ]] && ! mokutil --test-key "$MOK_PUB" 2>/dev/null | grep -qi "already enrolled"; then
        say "Secure Boot activo: hay que registrar la clave con la que DKMS firmó el módulo."
        echo "    Elige una contraseña temporal; la pedirá una sola vez la pantalla azul de MOK al reiniciar."
        mokutil --import "$MOK_PUB"
        NEED_MOK=1
    fi
fi

# --- aplicación -------------------------------------------------------------------
say "Instalando la aplicación…"
install -d /usr/share/nitrosense/nitrosense /usr/libexec/nitrosense
install -m 0644 "$SRC"/app/nitrosense/*.py /usr/share/nitrosense/nitrosense/
install -m 0755 "$SRC"/app/bin/nitrosense "$SRC"/app/bin/nitrosense-ctl /usr/bin/
install -m 0755 "$SRC"/data/fix-permissions /usr/libexec/nitrosense/
install -Dm 0644 "$SRC"/data/io.github.nitrosense.Linux.desktop /usr/share/applications/io.github.nitrosense.Linux.desktop
install -Dm 0644 "$SRC"/data/io.github.nitrosense.Linux.svg /usr/share/icons/hicolor/scalable/apps/io.github.nitrosense.Linux.svg
install -Dm 0644 "$SRC"/data/70-nitrosense.rules /etc/udev/rules.d/70-nitrosense.rules
install -Dm 0644 "$SRC"/data/nitrosense-restore.service /etc/systemd/system/nitrosense-restore.service
install -Dm 0755 "$SRC"/data/nitrosense-sleep /usr/lib/systemd/system-sleep/nitrosense
install -Dm 0644 "$SRC"/data/nitrosense-lights.service /usr/lib/systemd/user/nitrosense-lights.service
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
command -v restorecon >/dev/null && restorecon -R /usr/share/nitrosense /usr/libexec/nitrosense \
    /usr/bin/nitrosense /usr/bin/nitrosense-ctl /etc/udev/rules.d/70-nitrosense.rules \
    /etc/systemd/system/nitrosense-restore.service /usr/lib/systemd/system-sleep/nitrosense \
    /usr/lib/systemd/user/nitrosense-lights.service 2>/dev/null || true

# --- permisos: grupo nitrosense --------------------------------------------------
groupadd -f nitrosense
TARGET_USER="${SUDO_USER:-}"
if [[ -n "$TARGET_USER" && "$TARGET_USER" != root ]]; then
    usermod -aG nitrosense "$TARGET_USER"
    say "Usuario '$TARGET_USER' añadido al grupo nitrosense (efectivo al volver a iniciar sesión)."
fi
install -d -m 2775 -g nitrosense /var/lib/nitrosense

systemctl daemon-reload
udevadm control --reload

# --- cargar el driver ------------------------------------------------------------------
if [[ $NEED_MOK -eq 1 ]]; then
    say "Instalación completa. REINICIA, elige «Enroll MOK» → «Continue» → «Yes» e introduce la contraseña."
    echo "    Tras reiniciar, el driver se carga solo y podrás abrir «NitroSense» desde el menú."
    exit 0
fi

modprobe -r nitrosense 2>/dev/null || true
if modprobe nitrosense; then
    udevadm trigger --subsystem-match=wmi --subsystem-match=hwmon --action=add
    udevadm settle || true
    say "Driver cargado:"
    /usr/bin/nitrosense-ctl status || true
else
    warn "no se pudo cargar el módulo; revisa: sudo dmesg | grep -i nitrosense"
fi

say "Listo. Cierra sesión y vuelve a entrar para usar NitroSense sin sudo."
