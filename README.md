<div align="center">

<img src="data/io.github.nitrosense.Linux.svg" width="96" alt="NitroSense for Linux icon">

# Acer NitroSense for Linux

**Fan control, performance modes, CoolBoost and RGB keyboard for Acer Nitro laptops — on Linux.**

A from-scratch Linux replacement for Acer's Windows-only NitroSense app: a kernel driver
(DKMS) plus a native GTK4 / libadwaita app and a command-line tool.

![Platform](https://img.shields.io/badge/platform-Fedora%20Linux-51A2DA?logo=fedora&logoColor=white)
![Laptop](https://img.shields.io/badge/laptop-Acer%20Nitro%205%20AN515--57-83B81A)
![UI](https://img.shields.io/badge/UI-GTK4%20%2F%20libadwaita-4A86CF)
![License](https://img.shields.io/badge/license-GPL--2.0--or--later-blue)

**English** · [Español](README.es.md)

</div>

| Monitor | Fans |
|:-:|:-:|
| ![Monitor page](docs/screenshots/monitor.png) | ![Fans page](docs/screenshots/fans.png) |

> The app's interface is currently in **Spanish** (Monitor, Ventiladores = Fans,
> Iluminación = Lighting, Ajustes = Settings). The CLI commands and options are in English.

## Why

NitroSense can't simply be "recompiled" for Linux: it's a .NET WPF app that talks to a
Windows service, which talks to the BIOS through WMI. This project reimplements the whole
chain on Linux:

| Windows | Linux (this project) |
|---------|----------------------|
| `PSSvc.exe` + WMI | `kernel/nitrosense.c` — WMI kernel driver, built with DKMS |
| `NitroSense.exe` | `nitrosense` — GTK4 / libadwaita app |
| Registry `HKLM\SOFTWARE\OEM\NitroSense` | `/var/lib/nitrosense/state.json` |
| Service that re-applies settings | `nitrosense-restore.service` + suspend hook |
| — | `nitrosense-ctl` — command-line tool |

The firmware protocol was reverse-engineered from NitroSense 3.1.3056 (the .NET IL, the
native service and the BIOS ACPI tables) and is documented in [PROTOCOL.md](PROTOCOL.md)
(Spanish). **No Acer code or binaries are included.**

## Features

| Feature | Status | Notes |
|---------|:------:|-------|
| CPU / GPU / system temperatures and fan RPM | ✅ | also exposed through standard `hwmon` (`sensors`, GNOME, CoolerControl) |
| Performance modes: Quiet / Default / Extreme / Turbo | ✅ | only the modes your BIOS reports as supported |
| Fans: Auto / Max / Custom (per fan) | ✅ | standard `pwm` interface |
| CoolBoost | ✅ | |
| RGB keyboard: static 4 zones, breathing, neon, wave, shifting, zoom | ✅ | uses NitroSense's colour calibration |
| **Dynamic lighting** (Linux-only extra): Thermal, Aurora, CPU load, Ember | ✅ | animated in software over the 4 zones |
| Turn backlight off after 30 s idle | ✅ | |
| Windows/Menu key lock | ✅ | |
| LCD Overdrive | ✅ | |
| Settings restored on boot and after suspend | ✅ | |
| CPU/GPU overclocking | ❌ | the AN515-57 doesn't support it |
| GPU MUX switch | ❌ | not offered by the AN515-57 BIOS |
| NVIDIA Whisper Mode, DTS audio, macros | ❌ | Windows features; on Linux try EasyEffects / input-remapper |

## Compatibility

| Model | Status |
|-------|--------|
| **Acer Nitro 5 AN515-57** (BIOS V1.22) | ✅ Developed and running on this laptop |
| Other Acer Nitro / Predator models | ❓ Untested — may work if the BIOS exposes the same gaming WMI interface |

To check whether your laptop exposes the interface the driver needs:

```bash
cat /sys/class/dmi/id/product_name
ls /sys/bus/wmi/devices/ | grep -i 7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56
```

If the second command prints something, there's a good chance it works. The installer
refuses to run on models other than the AN515-57 unless you pass `--force` — **do so at
your own risk**, and please [open an issue](https://github.com/Sunshide12/Nitrosense-for-linux/issues)
with the result (working or not) so the list above can grow.

**Distributions:** the installer targets **Fedora Workstation** and dnf-based spins.
Fedora Atomic (Silverblue, Kinoite) is not supported. The driver and app themselves are
distro-agnostic; porting the installer to apt/pacman is a welcome contribution.

## Installation (Fedora)

1. **Update and reboot** if a new kernel was installed (the module is built against the
   running kernel):

   ```bash
   sudo dnf upgrade --refresh
   ```

2. **Clone and install:**

   ```bash
   git clone https://github.com/Sunshide12/Nitrosense-for-linux.git
   cd Nitrosense-for-linux
   sudo bash install.sh
   ```

   The installer pulls in `dkms`, `kernel-devel`, GTK4 and libadwaita, builds the module,
   creates the `nitrosense` group and adds you to it.

3. **Secure Boot** (enabled on most laptops): the installer enrolls the key DKMS signs the
   module with and asks you for a one-time password. Reboot, and on the blue
   *MOK management* screen choose **Enroll MOK → Continue → Yes** and type that password.
   You only do this once — future kernel updates rebuild and sign the module automatically.

4. **Log out and back in** (so the `nitrosense` group applies) and open **NitroSense** from
   your app menu.

### Uninstall

```bash
sudo bash uninstall.sh           # add --purge to also remove saved settings and the group
```

## Usage

### App

Four pages, like NitroSense: **Monitor** (performance mode + gauges), **Fans**,
**Lighting** and **Settings**. Changes apply instantly, are saved, and are re-applied at
boot and after resume.

### Command line

```bash
nitrosense-ctl status                    # temps, RPM and settings (--json for scripts)
nitrosense-ctl mode extreme              # quiet | default | extreme | turbo
nitrosense-ctl fan auto                  # auto | max | custom
nitrosense-ctl fan custom --cpu 70 --gpu auto
nitrosense-ctl coolboost on
nitrosense-ctl kb static --brightness 100 --zones ff0000 00ff00 0000ff ffffff
nitrosense-ctl kb wave --speed 5 --direction left-to-right --brightness 75
nitrosense-ctl kb breathing --color 00ffff --speed 3
nitrosense-ctl light list                # describe the dynamic modes
nitrosense-ctl light thermal             # thermal | aurora | load | ember
nitrosense-ctl light aurora --color1 ff00aa --color2 00ffd5 --speed 4 --brightness 75
nitrosense-ctl light off                 # back to the normal keyboard effect
nitrosense-ctl kbtimeout on              # backlight off after 30 s
nitrosense-ctl winkey off                # lock Windows/Menu keys
nitrosense-ctl restore                   # re-apply saved settings
```

### Dynamic lighting modes

The firmware only has six fixed effects, so these modes paint the 4 zones from software
(~15 fps) and react to the machine:

| Mode | What it does |
|------|--------------|
| `thermal` | left = CPU temperature, right = GPU, blue → green → amber → red gradient; pulses above 85 °C. If the dGPU is asleep (reports 0 °C) the CPU drives the whole keyboard |
| `aurora` | smooth gradient between two colours of your choice (`--color1`, `--color2`, `--speed`) |
| `load` | a bar that fills zone by zone with CPU usage |
| `ember` | warm fire-like flicker (`--speed`) |

They run as a user service (`nitrosense-lights.service`) that starts with your session.
Picking a normal effect from the app or with `nitrosense-ctl kb …` turns it off.

### Kernel interface

The driver is usable without the app (the device name may have a `-1`/`-8` suffix):

```
/sys/bus/wmi/devices/7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56*/
    operating_mode            quiet | default | extreme | turbo
    operating_mode_choices    modes supported by the BIOS
    kb_backlight              "<effect> <speed> <brightness> <direction> <RRGGBB>"
    kb_zone1 … kb_zone4       RRGGBB (raw, uncalibrated)
    win_key, lcd_overdrive    0 | 1
    hwmon/hwmonN/
        temp1_input (CPU), temp2_input (GPU), temp3_input (system)
        fan1_input (CPU), fan2_input (GPU)
        pwm1, pwm2            0-255
        pwm1_enable, pwm2_enable   0 max, 1 manual, 2 auto
/sys/bus/wmi/devices/61EF69EA-865C-4BC3-A502-A0DEBA0CB531*/
    coolboost, kb_timeout     0 | 1
```

Because fans use the standard `hwmon` `pwm` interface, tools like **CoolerControl** or
`fancontrol` can apply custom fan curves — something NitroSense itself can't do.

The in-tree `acer_wmi` module keeps working alongside it (Fn keys, Wi-Fi, …).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "El driver nitrosense no está cargado" (driver not loaded) | `sudo modprobe nitrosense`; if it fails, `sudo dmesg \| grep -i nitrosense` |
| `modprobe: Key was rejected by service` | Secure Boot: the MOK key isn't enrolled yet (install step 3) |
| "Modo solo lectura" (read-only mode) in the app | log out and back in; check `groups` includes `nitrosense` |
| Doesn't build after a kernel update | `sudo dnf install kernel-devel-$(uname -r)` and `sudo dkms autoinstall` |
| A performance mode fails | your BIOS doesn't support it — see `operating_mode_choices` |
| Dynamic lighting doesn't start | `systemctl --user status nitrosense-lights` |

## Testing

- **Real hardware:** running on an AN515-57 (BIOS V1.22), Fedora 44, kernel 7.2.11,
  Secure Boot enabled — the module builds with DKMS, is MOK-signed and loads; sensors, fan
  RPM and available modes read correctly.
- **Driver in a VM:** `tests/vm/run.sh` boots the kernel in QEMU with an SSDT containing
  the laptop's own ACPI methods (`WMBH`, `WMAA`, `GGSI`) extracted from your BIOS at test
  time; only EC registers and the SMI handler are emulated. 65 checks pass on Ubuntu 7.0
  and Fedora 44 (7.2.11) kernels, including module unload.
- **Userspace:** 36 unit tests over a simulated sysfs
  (`python3 -m unittest discover -s tests`) and a GUI smoke test (`tests/gui_smoke.py`).

Features handled through the BIOS SMI handler (fan behaviour, CoolBoost, backlight
timeout, Windows key, overdrive) send exactly the same request words as NitroSense, but
that handler can't be emulated outside the laptop. `re-tools/verify-windows.ps1`
(read-only, run as admin on Windows) compares the decoding against real values.

## Contributing

Issues and pull requests are welcome — especially:

- **Reports from other Nitro / Predator models**: include `cat /sys/class/dmi/id/product_name`,
  `cat /sys/class/dmi/id/bios_version`, `ls /sys/bus/wmi/devices/` and
  `nitrosense-ctl status --json`.
- Installers for other distributions (Debian/Ubuntu, Arch).
- An English translation of the app.

The reverse-engineering tools are in [`re-tools/`](re-tools): an IL disassembler
(`ildump.cs`), a Capstone-based string xref tool for the native service (`xref.py`) and a
`_WDG` decoder (`wdg.py`).

## License

[GPL-2.0-or-later](LICENSE). Independent project, not affiliated with or endorsed by Acer.
"Acer", "Nitro", "Predator" and "NitroSense" are trademarks of Acer Inc. Reverse
engineering was done for interoperability purposes.
