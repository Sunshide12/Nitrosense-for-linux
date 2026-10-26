<!--
SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
SPDX-License-Identifier: GPL-2.0-or-later
-->
# Roadmap

Where the project stands and where it could go. **Items under "Next" and "Ideas" are
proposals, not commitments**: the maintainer decides priorities. If you are an AI agent, read
[AGENTS.md](AGENTS.md) first and do not start a roadmap item unless asked to.

## Done (1.0.0)

- WMI kernel driver (DKMS): sensors via hwmon, operating mode, fans (auto / max / custom),
  CoolBoost, 4-zone RGB keyboard with the six firmware effects, backlight timeout, Windows key
  lock, LCD overdrive.
- `nitrosense-ctl` CLI and GTK4 / libadwaita GUI (Spanish).
- Dynamic lighting modes painted by software: thermal, aurora, CPU load, ember.
- Saved settings restored at boot (udev → systemd) and after suspend (sleep hook).
- Fedora installer / uninstaller, Secure Boot (MOK) handling.
- 36 unit tests on a fake sysfs, a QEMU test with emulated firmware, a Fedora container check.
- Firmware protocol documented in `PROTOCOL.md`.

## Known limitations

- Verified on one laptop only: **Acer Nitro 5 AN515-57**. Other Nitro / Predator models are
  untested (the installer needs `--force`).
- Fedora only (`dnf`); Fedora Atomic (Silverblue / Kinoite) is not supported.
- The GUI and CLI are Spanish-only; `PROTOCOL.md` is Spanish-only.
- The MUX switch is documented in `PROTOCOL.md` but not implemented (the AN515-57 reports it
  as unsupported).
- Battery boost state is documented (`GetGamingSysInfo(0x02)`, byte 5) but not exposed.
- No CI: tests run by hand.
- Settings are restored by userspace; the driver itself has no power-management callbacks.

## Next (proposed, roughly by value)

1. **CI** — GitHub Actions running `python3 -m unittest discover -s tests` and the Fedora
   container check (`tests/fedora/check.sh`) on every push and PR.
2. **Model support** — collect reports from other Nitro / Predator owners; turn the single
   AN515-57 check in `install.sh` into a table of known-good DMI product names; make
   per-model differences (supported modes, fan indices, `bk_hotkey`) data instead of code.
3. **Packaging** — RPM (COPR) so users don't clone and run a script; later Debian/Ubuntu and
   Arch (AUR) packages, which also means making `install.sh` distro-aware or replacing it.
4. **App identity** — the app id `io.github.nitrosense.Linux` claims a GitHub namespace that is
   not the maintainer's; a Flatpak/Flathub-style id would be `io.github.sunshide12.<Name>`.
   Add AppStream metadata (`.metainfo.xml`). Fix the `Categories` hint reported by
   `desktop-file-validate`.
5. **Internationalisation** — move GUI/CLI strings to gettext, ship `es` and `en`.
6. **English `PROTOCOL.md`** so non-Spanish speakers can help with other models.

## Ideas (not scheduled)

- Expose battery boost and, where supported, the MUX switch.
- Custom fan curves (the firmware has a fan table; today only the table chosen by the
  operating mode is used).
- Restore settings from the driver on resume instead of the userspace hook.
- More dynamic lighting modes (audio-reactive, per-app); a plugin point in `effects.py`.
- GUI tests beyond the screenshot smoke test.
- A GNOME Shell quick-settings toggle for operating mode.
- Evaluate sending the driver upstream to the Linux kernel (`drivers/platform/x86/`), which
  needs kernel coding-style cleanup, a sysfs ABI document and coordination with `acer-wmi`.

## How to propose a change

Open an issue describing the feature and the laptop model it was tested on. Contribution
rules (DCO sign-off, no Acer code) are in [CONTRIBUTING.md](CONTRIBUTING.md).
