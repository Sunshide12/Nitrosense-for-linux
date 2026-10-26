#!/bin/busybox sh
# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
# PID 1 of the test VM: load the driver against the emulated firmware and check it.
/bin/busybox --install -s /bin
mkdir -p /proc /sys /dev
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev

insmod /wmi.ko
insmod /nitrosense.ko
sleep 1

FAILS=0
check() {
    if [ "$2" = "$3" ]; then
        echo "PASS  $1"
    else
        echo "FAIL  $1: esperado '$2', obtenido '$3'"
        FAILS=$((FAILS + 1))
    fi
}
# write <file> <value>: prints ok/err depending on the kernel's answer
write() { echo "$2" > "$1" 2>/dev/null && echo ok || echo err; }

G=$(ls -d /sys/bus/wmi/devices/7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56* 2>/dev/null | head -n1)
A=$(ls -d /sys/bus/wmi/devices/61EF69EA-865C-4BC3-A502-A0DEBA0CB531* 2>/dev/null | head -n1)
H=$(ls -d "$G"/hwmon/hwmon* 2>/dev/null | head -n1)

echo "--- dispositivos WMI: $(ls /sys/bus/wmi/devices | tr '\n' ' ')"
check "driver ligado al GUID de juego"   nitrosense         "$(basename "$(readlink "$G/driver")")"
check "driver ligado a APGeAction"       nitrosense-generic "$(basename "$(readlink "$A/driver")")"
check "hwmon registrado"                 nitrosense         "$(cat "$H/name")"

echo "--- sensores (GetGamingSysInfo -> GGSI real)"
check "temp1 CPU"        45000 "$(cat "$H/temp1_input")"
check "temp2 GPU"        55000 "$(cat "$H/temp2_input")"
check "temp3 sistema"    32000 "$(cat "$H/temp3_input")"
check "etiqueta temp2"   GPU   "$(cat "$H/temp2_label")"
check "fan1 RPM (auto)"  2200  "$(cat "$H/fan1_input")"
check "fan2 RPM (auto)"  2200  "$(cat "$H/fan2_input")"

echo "--- modo de funcionamiento (misc 0x0A/0x0B + tabla de ventiladores)"
check "modos soportados" "quiet default extreme turbo" "$(cat "$G/operating_mode_choices")"
check "modo inicial"     default "$(cat "$G/operating_mode")"
check "escribir extreme" ok      "$(write "$G/operating_mode" extreme)"
check "leer extreme"     extreme "$(cat "$G/operating_mode")"
check "tabla EC extreme=3" 33000 "$(cat "$H/temp3_input")"
check "escribir quiet"   ok      "$(write "$G/operating_mode" quiet)"
check "tabla EC quiet=1" 31000   "$(cat "$H/temp3_input")"
check "escribir turbo (número 5)" ok "$(write "$G/operating_mode" 5)"
check "leer turbo"       turbo   "$(cat "$G/operating_mode")"
check "tabla EC turbo=2" 32000   "$(cat "$H/temp3_input")"
check "modo inválido rechazado" err "$(write "$G/operating_mode" ludicrous)"
check "modo 2 no soportado"     err "$(write "$G/operating_mode" 2)"

echo "--- ventiladores (SetGamingFanBehavior / SetGamingFanSpeed)"
check "pwm1_enable inicial auto" 2  "$(cat "$H/pwm1_enable")"
check "CPU a máximo"     ok    "$(write "$H/pwm1_enable" 0)"
check "fan1 RPM máximo"  5800  "$(cat "$H/fan1_input")"
check "GPU sigue en auto" 2200 "$(cat "$H/fan2_input")"
check "pwm1=128 en espera" ok  "$(write "$H/pwm1" 128)"
check "CPU personalizado" ok   "$(write "$H/pwm1_enable" 1)"
check "fan1 RPM 50 %"    2900  "$(cat "$H/fan1_input")"
check "pwm1 leído del EC" 128  "$(cat "$H/pwm1")"
check "pwm1=255 en vivo" ok    "$(write "$H/pwm1" 255)"
check "fan1 RPM 100 %"   5800  "$(cat "$H/fan1_input")"
check "GPU personalizado 30 %" ok "$(write "$H/pwm2" 77)"
check "GPU modo manual"  ok    "$(write "$H/pwm2_enable" 1)"
check "fan2 RPM 30 %"    1740  "$(cat "$H/fan2_input")"
check "CPU vuelve a auto" ok   "$(write "$H/pwm1_enable" 2)"
check "fan1 RPM auto"    2200  "$(cat "$H/fan1_input")"
check "pwm fuera de rango" err "$(write "$H/pwm1" 300)"
check "pwm_enable inválido" err "$(write "$H/pwm1_enable" 7)"

echo "--- teclado RGB (SetGamingKBBacklight / SetGamingRgbKb)"
check "efecto ola"       ok    "$(write "$G/kb_backlight" "3 5 75 1 ff8000")"
check "leer efecto ola"  "3 5 75 1 ff8000" "$(cat "$G/kb_backlight")"
check "efecto estático"  ok    "$(write "$G/kb_backlight" "0 0 100 0 000000")"
check "leer estático"    "0 0 100 0 000000" "$(cat "$G/kb_backlight")"
check "efecto inválido"  err   "$(write "$G/kb_backlight" "9 0 0 0 000000")"
check "zona 1 verde"     ok    "$(write "$G/kb_zone1" 00ff00)"
check "zona 3 color"     ok    "$(write "$G/kb_zone3" 123456)"
check "leer zona 1"      00ff00 "$(cat "$G/kb_zone1")"
check "leer zona 3"      123456 "$(cat "$G/kb_zone3")"
check "zona 2 intacta"   000000 "$(cat "$G/kb_zone2")"

echo "--- perfil (SetGamingProfile / GetGamingProfile)"
check "win_key sin escribir" unknown "$(cat "$G/win_key")"
check "bloquear tecla Windows" ok "$(write "$G/win_key" 0)"
check "leer win_key"     0     "$(cat "$G/win_key")"
check "overdrive inicial" 0    "$(cat "$G/lcd_overdrive")"
check "activar overdrive" ok   "$(write "$G/lcd_overdrive" 1)"
check "leer overdrive"   1     "$(cat "$G/lcd_overdrive")"

echo "--- APGeAction (SetFunction / GetFunction)"
check "coolboost inicial" 0    "$(cat "$A/coolboost")"
check "activar coolboost" ok   "$(write "$A/coolboost" 1)"
check "leer coolboost"   1     "$(cat "$A/coolboost")"
check "desactivar coolboost" ok "$(write "$A/coolboost" 0)"
check "leer coolboost off" 0   "$(cat "$A/coolboost")"
check "kb_timeout inicial" 0   "$(cat "$A/kb_timeout")"
check "activar kb_timeout" ok  "$(write "$A/kb_timeout" 1)"
check "leer kb_timeout"  1     "$(cat "$A/kb_timeout")"

echo "--- descarga del módulo"
check "rmmod"            ok    "$(rmmod nitrosense 2>/dev/null && echo ok || echo err)"
check "dispositivos liberados" "" "$(ls "$G/driver" 2>/dev/null)"

echo "--- dmesg"
dmesg | grep -iE "nitrosense|ACPI (Error|Warning)|AE_|BUG|Oops|WARNING" | sed 's/^/    /'
check "sin errores ACPI/kernel" 0 "$(dmesg | grep -ciE 'ACPI Error|AE_AML|BUG:|Oops|WARNING:')"

echo "RESULT fails=$FAILS"
poweroff -f
