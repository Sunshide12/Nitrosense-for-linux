#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
# Runs inside a Fedora container: installs the same packages as install.sh, builds
# the module with DKMS against Fedora's kernel-devel and runs the Python tests.
#
#   podman run --rm -v "$PWD:/src:ro" registry.fedoraproject.org/fedora:latest bash /src/tests/fedora/check.sh
set -euo pipefail

echo "== $(cat /etc/fedora-release)"
dnf install -y -q --setopt=install_weak_deps=False dkms gcc make mokutil python3 python3-gobject python3-cairo gobject-introspection gtk4 libadwaita kernel-devel >/dev/null
echo "== paquetes de install.sh disponibles e instalados"

KV="$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' kernel-devel | sort -V | tail -n1)"
echo "== kernel-devel $KV"

cp -r /src/kernel /usr/src/nitrosense-1.0.0
dkms add -m nitrosense -v 1.0.0 >/dev/null
if ! dkms build -m nitrosense -v 1.0.0 -k "$KV" --kernelsourcedir "/usr/src/kernels/$KV" >/tmp/dkms.log 2>&1; then
    cat /tmp/dkms.log /var/lib/dkms/nitrosense/1.0.0/build/make.log
    exit 1
fi
tail -n 2 /tmp/dkms.log
find /var/lib/dkms/nitrosense -name 'nitrosense.ko*' -exec modinfo {} \; | grep -E '^(filename|vermagic|alias)'

python3 - <<'EOF'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
print(f"== GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}, "
      f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}")
for name in ("AboutDialog", "SwitchRow", "ToolbarView", "Banner"):
    assert hasattr(Adw, name), name
assert hasattr(Gtk, "ColorDialogButton")
print("== widgets usados por la GUI presentes")
EOF

cp -r /src /tmp/src
cd /tmp/src
python3 -m unittest discover -s tests 2>&1 | tail -n 3
python3 -c "import sys; sys.path.insert(0, 'app'); import nitrosense.gui" && echo "== nitrosense.gui importa sin errores"
