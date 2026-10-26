#!/usr/bin/env bash
# End-to-end test of the kernel driver in a QEMU VM with emulated Acer firmware.
#
#   tests/vm/run.sh <decompiled real SSDT with WMBH (SSDB.dsl)> [kernel version]
#
# Needs: qemu-system-x86_64, iasl, busybox (static), zstd, python3, the kernel
# image in /boot and its headers/modules. KVM is used when available.
# To test another distribution's kernel set VMLINUZ, WMI_KO and NITROSENSE_KO
# (a prebuilt module) instead.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DSL="${1:?pasa la ruta del SSDT decompilado (SSDB.dsl)}"
KVER="${2:-$(ls /lib/modules | sort -V | tail -n1)}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== kernel $KVER, trabajo en $WORK"
python3 -I "$HERE/make_ssdt.py" "$DSL" "$WORK/acer-sim.asl"
iasl -p "$WORK/acer-sim" "$WORK/acer-sim.asl" >"$WORK/iasl.log" 2>&1 || { cat "$WORK/iasl.log"; exit 1; }

if [[ -z "${NITROSENSE_KO:-}" ]]; then
    cp -r "$ROOT/kernel" "$WORK/kernel"
    make -s -C "$WORK/kernel" KVER="$KVER" >/dev/null
    NITROSENSE_KO="$WORK/kernel/nitrosense.ko"
fi
VMLINUZ="${VMLINUZ:-/boot/vmlinuz-$KVER}"
WMI_KO="${WMI_KO:-$(find "/lib/modules/$KVER" -name 'wmi.ko*' | head -n1)}"

mkdir -p "$WORK/initrd/bin"
cp "$(command -v busybox)" "$WORK/initrd/bin/busybox"
cp "$HERE/vm-init.sh" "$WORK/initrd/init"
chmod +x "$WORK/initrd/init"
cp "$NITROSENSE_KO" "$WORK/initrd/nitrosense.ko"
case "$WMI_KO" in
    *.zst) zstd -qdc "$WMI_KO" >"$WORK/initrd/wmi.ko" ;;
    *.xz)  xz -dc "$WMI_KO" >"$WORK/initrd/wmi.ko" ;;
    *)     cp "$WMI_KO" "$WORK/initrd/wmi.ko" ;;
esac
(cd "$WORK/initrd" && find . | cpio -o -H newc --quiet | gzip) >"$WORK/initrd.gz"

ACCEL=()
[[ -w /dev/kvm ]] && ACCEL=(-enable-kvm -cpu host)
timeout 300 qemu-system-x86_64 "${ACCEL[@]}" -m 512 -nographic -no-reboot \
    -kernel "$VMLINUZ" -initrd "$WORK/initrd.gz" \
    -acpitable file="$WORK/acer-sim.aml" \
    -append "console=ttyS0 loglevel=4 panic=-1" | tee "$WORK/console.log" | grep -aE '^(PASS|FAIL|---|==|RESULT|    )'

cp "$WORK/console.log" /tmp/nitrosense-vm-console.log
grep -aq "RESULT fails=0" "$WORK/console.log"
