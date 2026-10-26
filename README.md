<div align="center">

<img src="data/io.github.nitrosense.Linux.svg" width="96" alt="NitroSense for Linux icon">

# Acer NitroSense for Linux

**Fan control, performance modes, CoolBoost and RGB keyboard for Acer Nitro laptops — on Linux.**

![Platform](https://img.shields.io/badge/platform-Fedora%20Linux-51A2DA?logo=fedora&logoColor=white)
![UI](https://img.shields.io/badge/UI-GTK4%20%2F%20libadwaita-4A86CF)
![License](https://img.shields.io/badge/license-GPL--2.0--or--later-blue)

**English** · [Español](README.es.md)

</div>

| Monitor | Fans |
|:-:|:-:|
| ![Monitor page](docs/screenshots/monitor.png) | ![Fans page](docs/screenshots/fans.png) |

## Features

- Temperatures and fan speeds (CPU, GPU, system)
- Performance modes: Quiet, Default, Extreme, Turbo
- Fans: Auto, Max or Custom speed per fan, plus CoolBoost
- RGB keyboard: static 4 zones, breathing, neon, wave, shifting, zoom
- Extra dynamic lighting modes: Thermal, Aurora, CPU load, Ember
- Backlight timeout, Windows key lock, LCD Overdrive
- Settings are restored at boot and after suspend

> The app's interface is in **Spanish**. The command-line tool uses English commands.

## Tested on

| Laptop | Status |
|--------|--------|
| Acer Nitro 5 **AN515-57** | ✅ Works |
| Other Acer Nitro / Predator models | ❓ Untested |

Other models may work if they have the same firmware interface. Check with:

```bash
ls /sys/bus/wmi/devices/ | grep -i 7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56
```

If it prints something, you can try installing with `--force` (at your own risk). Please
[open an issue](https://github.com/Sunshide12/Nitrosense-for-linux/issues) to say whether it
worked on your model.

## Installation (Fedora)

```bash
sudo dnf upgrade --refresh        # reboot afterwards if the kernel was updated
git clone https://github.com/Sunshide12/Nitrosense-for-linux.git
cd Nitrosense-for-linux
sudo bash install.sh
```

- **Secure Boot:** the installer asks you for a one-time password. Reboot, and on the blue
  *MOK management* screen choose **Enroll MOK → Continue → Yes** and type it. Only needed once.
- **Log out and back in**, then open **NitroSense** from your app menu.

To uninstall: `sudo bash uninstall.sh` (add `--purge` to also delete saved settings).

## Usage

Open **NitroSense** from the app menu, or use the command line:

```bash
nitrosense-ctl status                    # temperatures, fans and settings
nitrosense-ctl mode turbo                # quiet | default | extreme | turbo
nitrosense-ctl fan max                   # auto | max | custom
nitrosense-ctl fan custom --cpu 70 --gpu auto
nitrosense-ctl coolboost on
nitrosense-ctl kb static --brightness 100 --zones ff0000 00ff00 0000ff ffffff
nitrosense-ctl kb wave --speed 5 --brightness 75
nitrosense-ctl light thermal             # thermal | aurora | load | ember | off
```

Run `nitrosense-ctl --help` for every option.

## How it was made

Acer's NitroSense is a Windows-only .NET app that talks to the BIOS through a Windows
service and WMI, so it can't just be recompiled for Linux. Its protocol was
reverse-engineered (the app, its service and the laptop's ACPI tables) and reimplemented
from scratch:

- `kernel/` — a Linux WMI driver (built with DKMS) that talks to the BIOS
- `app/` — the GTK4 / libadwaita app and the `nitrosense-ctl` command-line tool

The protocol is documented in [PROTOCOL.md](PROTOCOL.md) and the tools used are in
[`re-tools/`](re-tools). No Acer code or binaries are included.

## License

Copyright (C) 2026 Steban Martínez Gutiérrez (Sunshide.com). Free software under the
[GNU GPL v2 or later](LICENSE) (`GPL-2.0-or-later`): you may use, study, modify and share it,
and if you distribute it (modified or not) you must keep the copyright notices and license, and
publish your changes under the same license.

**Hardware warning:** this software writes to your laptop's firmware (fans, performance modes).
It comes with no warranty, as stated in the license; use it at your own risk.

NitroSense is a trademark of Acer Inc. This is an independent project, not affiliated with,
endorsed by or sponsored by Acer.

Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).
