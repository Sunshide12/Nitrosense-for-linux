<!--
SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
SPDX-License-Identifier: GPL-2.0-or-later
-->
# AGENTS.md — guide for AI agents working on this repo

Read this before changing anything. It describes how the project is built, how the pieces
talk to each other, how to extend it, and what must never be done. Humans can read it too.
For what is planned, see [ROADMAP.md](ROADMAP.md). For the firmware protocol, see
[PROTOCOL.md](PROTOCOL.md) (in Spanish).

The maintainer writes in **Spanish**: answer them in Spanish. Code, comments, docstrings and
commit messages are in **English**. User-facing strings (GUI, CLI help, error messages) are in
**Spanish**.

## 1. What this project is

Acer's NitroSense is a Windows-only app. This repo reimplements its hardware layer for Linux
on Acer Nitro laptops (tested only on the **Nitro 5 AN515-57**):

- `kernel/nitrosense.c` — a Linux WMI driver that talks to the BIOS/EC.
- `app/nitrosense/` — Python userspace: hardware backend, saved state, CLI, GTK4/libadwaita GUI,
  and a keyboard-animation daemon.
- `data/` — udev rule, systemd units, desktop entry, icon.
- `install.sh` / `uninstall.sh` — Fedora installer (DKMS module + files + group + services).

License: **GPL-2.0-or-later**. Independent project, not affiliated with Acer ("NitroSense" is
Acer's trademark; the maintainer decided to keep the name).

## 2. Hard rules

1. **This software writes to laptop firmware** (fans, power limits, EC). A wrong value can
   overheat or hang a machine. Never load the driver or run `nitrosense-ctl` commands that
   change settings against real hardware unless the maintainer asked for it (read-only
   `nitrosense-ctl status` is fine). Everything in the test suite runs on a fake sysfs or a
   QEMU VM with emulated firmware. Do not "try it" on the host.
2. **No Acer code or binaries in the repo.** No decompiled source, no copied strings dumps, no
   Windows installers. Protocol *facts* (GUIDs, method ids, bit layouts) go in `PROTOCOL.md` in
   your own words. The code is a from-scratch reimplementation; keep it that way.
3. **Licensing.** Every new source/data file starts with
   ```
   SPDX-FileCopyrightText: <year> <author>
   SPDX-License-Identifier: GPL-2.0-or-later
   ```
   (comment syntax of the file type; for scripts, after the shebang). Do not add dependencies
   or assets (fonts, icons, images, code) whose license is not compatible with GPL-2.0-or-later.
   Do not change `LICENSE` or existing copyright lines.
4. **Git.** Commit messages in English. Contributors sign off commits (`git commit -s`, DCO) —
   that sign-off belongs to the human who directs you, never invent an identity. **Do not add
   `Co-Authored-By`, `Claude-Session`, "Generated with …" or any other AI trailer** to commits
   or PR descriptions: the maintainer does not want AI tools listed as contributors. Do not push
   or open PRs unless asked.
5. **Don't rename the project, the `nitrosense` module/group/paths or the app id**
   (`io.github.nitrosense.Linux`) without being asked: they are referenced in the kernel
   module, udev rule, systemd units, installer, desktop file and tests.
6. **Verify, don't assume.** If you did not run a test, build or command, say so. The firmware
   semantics in `PROTOCOL.md` were reverse-engineered on one machine: mark anything beyond
   that as a hypothesis.

## 3. Architecture

```
 BIOS / EC (ACPI, WMI blocks WMBH + WMAA)
        ▲
        │ wmidev_evaluate_method()
 kernel/nitrosense.c ── sysfs attributes + hwmon device
        ▲
        │ plain file reads/writes                 (NITROSENSE_SYSFS env var redirects /sys in tests)
 app/nitrosense/hw.py            Hardware: the only module that touches sysfs
        ▲
 ┌──────┼───────────────┬──────────────────┐
 state.py            cli.py             gui.py
 saved settings      nitrosense-ctl     GTK4 / libadwaita window
 + apply()           ▲                  │
        ▲            │                  │
        └────────── lightd.py ◄─────────┘  starts/stops the animation user service
                       │ uses effects.py (pure colour maths)
```

Rules that follow from this picture:

- **Only `hw.py` knows sysfs paths and file formats.** CLI, GUI and lightd go through `Hardware`.
- **`state.py` is the single source of saved settings.** CLI and GUI both do
  *change hardware → mutate state dict → `st.save()`*. `st.apply()` re-applies everything.
- **`effects.py` is pure** (time + readings in, colours out). Keep it free of I/O so it stays
  trivially testable.
- **GUI and CLI contain no protocol knowledge.** Anything about bytes/bits belongs in the
  kernel driver (and `PROTOCOL.md`).

### File map

| Path | Role |
|------|------|
| `kernel/nitrosense.c` | WMI driver. Two `wmi_driver`s: `AcerGamingFunction` (GUID `7A4DDFE7-…`) and `APGeAction` (GUID `61EF69EA-…`) |
| `kernel/Makefile`, `kernel/dkms.conf` | Out-of-tree build / DKMS packaging |
| `app/nitrosense/hw.py` | `Hardware` class, dataclasses `Sensors`/`FanState`/`KbBacklight`, keyboard effect tables, `calibrate()` |
| `app/nitrosense/state.py` | `DEFAULTS`, load/save JSON, `apply()` and its per-feature helpers |
| `app/nitrosense/cli.py` | `nitrosense-ctl` (argparse). `build_parser()` + one `cmd_*` per subcommand |
| `app/nitrosense/gui.py` | `NitroSenseWindow` (4 pages: monitor, fans, lighting, settings) and `NitroSenseApp` |
| `app/nitrosense/effects.py` | Dynamic lighting modes (`MODES`, `render()`) |
| `app/nitrosense/lightd.py` | Animation loop (`nitrosense-ctl light run`) and user-service control |
| `app/bin/nitrosense`, `nitrosense-ctl` | Launchers; they add `/usr/share/nitrosense` to `sys.path` |
| `data/70-nitrosense.rules` | udev: group permissions + start the restore service when the driver appears |
| `data/fix-permissions` | Makes the writable sysfs attributes group-writable. **Has a hard-coded attribute list** |
| `data/nitrosense-restore.service` | Oneshot `nitrosense-ctl restore` at boot / driver load |
| `data/nitrosense-sleep` | systemd-sleep hook: restore after resume (the EC forgets settings) |
| `data/nitrosense-lights.service` | **User** service running the animation loop |
| `tests/` | See §6 |
| `re-tools/` | The maintainer's reverse-engineering helpers (IL dumper, PE xref, WMI `_WDG` decoder, Windows verify script). Not part of the product |

## 4. Key interfaces

### sysfs ABI exposed by the driver

Under `/sys/bus/wmi/devices/7A4DDFE7-…/` (AcerGamingFunction):

| Attribute | Access | Format |
|-----------|--------|--------|
| `operating_mode` | rw | `quiet` \| `default` \| `extreme` \| `turbo` |
| `operating_mode_choices` | ro | space-separated modes the firmware supports |
| `kb_backlight` | rw | `effect speed brightness direction RRGGBB` (numbers; effect 0-5) |
| `kb_zone1`…`kb_zone4` | rw | `RRGGBB` (static colour of a zone) |
| `win_key` | rw | `0` / `1`; reads `unknown` until written (firmware has no getter) |
| `lcd_overdrive` | rw | `0` / `1` |
| `hwmon/hwmonN/` (name `nitrosense`) | | `temp1..3_input` (CPU, GPU, system, m°C), `fan1..2_input` (RPM), `pwm1..2` (0-255), `pwm1..2_enable` (0 = full speed, 1 = manual, 2 = automatic) |

Under `/sys/bus/wmi/devices/61EF69EA-…/` (APGeAction): `coolboost` (rw, 0/1) and
`kb_timeout` (rw, 0/1). Module parameter `bk_hotkey` (default 132) is the NitroSense
`BK_Hotkey_Number` used by `kb_timeout`.

### Saved state (`state.py`)

- Path: `/var/lib/nitrosense/state.json` (shared, group `nitrosense`, setgid dir) when writable,
  else `~/.config/nitrosense/state.json` (`state_path()`).
- `DEFAULTS` defines the schema. **`_merge()` only keeps keys that exist in `DEFAULTS`**, so a
  new setting *must* be added there or it will be silently dropped on load.
- `apply()` applies, in order: operating mode, fans, CoolBoost, keyboard, kb timeout, Windows
  key, then LCD overdrive only if not `None`. A failing step is collected and the rest still run.
- `lcd_overdrive: None` means "leave the firmware value alone".

### Dynamic lighting

`keyboard.dynamic.mode` in the state selects a software-animated mode (`thermal`, `aurora`,
`load`, `ember`) or `None` (use the firmware effect). `lightd.set_dynamic()` starts/stops the
user service `nitrosense-lights.service`; the loop reloads settings on `SIGHUP`. When a dynamic
mode is active, `st.apply_keyboard()` only sets a *static* base at the chosen brightness and
leaves the zone colours to the loop.

## 5. Conventions

### Python (`app/`, `tests/`)

- Python 3, `from __future__ import annotations`, type hints, stdlib + PyGObject/cairo only.
  Do not add pip dependencies: the installer only uses `dnf` packages.
- Raise `DriverMissing` / `NoPermission` (from `hw.py`) for expected failures; the CLI turns
  them into exit code 2 and OSError from the firmware into exit code 1.
- GUI patterns in `gui.py`:
  - route hardware changes through `self.do(action, *args)` — it skips while `self._loading`,
    saves state and turns exceptions into toasts;
  - wrap continuous controls (sliders, colour pickers) in `self.debounce(key, fn)`;
  - set `self._loading = True` while programmatically updating widgets, so handlers don't
    write back to the hardware;
  - the 2 s `_refresh()` poll only reads.
- Colours are `RRGGBB` lowercase hex strings everywhere.

### Kernel C (`kernel/`)

- Linux kernel coding style (tabs, 80–100 cols), prefix `ns_` for everything.
- **One global mutex `ns_lock` guards every firmware call** (both WMI blocks end up in the
  same EC/SMI path). Helpers that call the firmware use `lockdep_assert_held(&ns_lock)`; sysfs
  handlers take the lock around them.
- Firmware calls go through `ns_call()` / `ns_call_int()`. Parameters are little-endian buffers
  of the MOF type size (4 bytes for `uint32`, 8 for `uint64`); the low byte of every reply is a
  status (0 = success). Use `sysfs_emit()` in `show` handlers.
- Check changes with `make -C kernel` (needs `kernel-devel`) and, if possible, the VM test.
  Run `scripts/checkpatch.pl --no-tree -f kernel/nitrosense.c` when available.

### Shell / system files

- `install.sh` and `uninstall.sh` must stay symmetric: every file `install.sh` places must be
  removed by `uninstall.sh`, and vice versa.
- Installer messages are Spanish. Fedora Atomic is deliberately unsupported (read-only `/usr`).

## 6. Testing

| What | Command | Needs |
|------|---------|-------|
| Python unit tests (36) | `python3 -m unittest discover -s tests -v` | nothing (fake sysfs) |
| Kernel module builds | `cp -r kernel /tmp/k && make -C /tmp/k` | `kernel-devel` for the running kernel. Build in a copy so no artefacts land in the repo |
| GUI smoke test (screenshots of each page) | `python3 tests/gui_smoke.py <outdir>` | a Wayland/X display |
| Fedora container check (DKMS build + imports + tests) | `podman run --rm -v "$PWD:/src:ro" registry.fedoraproject.org/fedora:latest bash /src/tests/fedora/check.sh` | podman |
| Driver end-to-end in QEMU with emulated firmware | `tests/vm/run.sh <SSDB.dsl> [kernel]` | qemu, iasl, busybox, and the *decompiled real SSDT* of the laptop (not in the repo) |

How the unit tests work: `FakeSysfsTest` builds a fake `/sys` tree with `make_tree()` (files
listed in `GAMING_FILES`, `HWMON_FILES`, `GENERIC_FILES`), points `NITROSENSE_SYSFS` at it and
patches `state.SYSTEM_STATE` to a temp file. **If you add a sysfs attribute, add it to those
dicts**, otherwise `Hardware` reads will fail in tests.

Before saying a change is done: run the unit tests; if you touched `kernel/`, build it; if you
touched `gui.py`, at least `python3 -I -m py_compile` it and, if a display exists, run the
smoke test; if you touched shell scripts, `bash -n` them. Report anything you could not run.

## 7. Recipes

### A. Expose a new firmware feature end to end

Example: a new on/off setting. Touch these places, in this order:

1. **`PROTOCOL.md`** — document the WMI method, arguments and reply bits (mark what is verified
   on hardware vs. inferred).
2. **`kernel/nitrosense.c`** — add the method id to the enum if new, a `*_show`/`*_store` pair
   (taking `ns_lock`), a `DEVICE_ATTR_RW(...)`, and list it in the right `*_attrs[]` array
   (`ns_gaming_attrs` or `ns_generic_attrs`).
3. **`data/fix-permissions`** — add the attribute name to the list, or unprivileged users
   (group `nitrosense`) cannot write it.
4. **`app/nitrosense/hw.py`** — `get_x()` / `set_x()` using `_get`/`_set`.
5. **`app/nitrosense/state.py`** — default in `DEFAULTS`; a step in `apply()`.
6. **`app/nitrosense/cli.py`** — a subcommand (for on/off settings add a tuple to the
   `cmd_toggle` loop in `build_parser()`) and, if useful, a line in `cmd_status`.
7. **`app/nitrosense/gui.py`** — a row in the relevant `_build_*` page, a handler using
   `self.do(...)`, and loading in `_load_from_state()`.
8. **Tests** — add the file to the fake sysfs dicts and a test in `HardwareTest` / `CliTest` /
   `StateTest`.
9. **README.md and README.es.md** — update the feature list.

### B. Add a dynamic lighting mode

1. In `effects.py` write a pure function `my_mode(t, ctx) -> list[colour]` returning
   `ZONES` (4) colours, register it in `_RENDERERS`, and add `"my_mode": (label, description)`
   to `MODES` (Spanish strings).
2. The CLI (`light` choices) and GUI (`DYNAMIC`) read `MODES`, so they pick it up
   automatically. New sensor input? Extend `effects.Context` and fill it in `lightd.run()`.
3. Add tests to `EffectsTest` (every mode must render 4 colours — `test_every_mode_renders_four_colours`
   covers the basics) and update the README feature list.

### C. Add or change a firmware keyboard effect

Effect ids live in `hw.KB_EFFECTS` (and the matching names in `gui.EFFECTS`). The sets
`KB_EFFECTS_WITH_COLOR` / `KB_EFFECTS_WITH_DIRECTION` control which fields are sent. Byte
layout of the firmware call is in `PROTOCOL.md` ("Teclado RGB de 4 zonas"); the driver packs it
in `kb_backlight_store()` (the wave effect needs the `0x08` flag).

### D. Support another laptop model

1. Check the machine exposes the WMI GUIDs (`ls /sys/bus/wmi/devices/ | grep -i 7A4DDFE7-…`).
2. Differences usually live in: supported modes (`GetGamingMiscSetting(0x0A)` mask → the
   driver's `supported_modes`), sensor ids, the `bk_hotkey` value, fan count/indices
   (`ns_fans[]`), and the DMI check in `install.sh` (currently AN515-57 only; others need
   `--force`).
3. Do **not** claim support in the README until someone confirmed it on real hardware; use the
   "Tested on" table.

### E. Bump the version

The version string `1.0.0` is duplicated and must be changed together:
`app/nitrosense/__init__.py` (`__version__`), `kernel/nitrosense.c` (`MODULE_VERSION`),
`kernel/dkms.conf` (`PACKAGE_VERSION`), `install.sh` (`PKG_VERSION` — never name it `VERSION`,
`/etc/os-release` overwrites it) and `tests/fedora/check.sh` (several places).

### F. Add an installed file

Add it to `install.sh` (with the right mode; `restorecon` list if it is executable/system) and
to `uninstall.sh`. If it is a Python module in `app/nitrosense/`, nothing else: the installer
copies `*.py`.

## 8. Gotchas (learned the hard way)

- **Red channel calibration.** NitroSense multiplies red by 0.8 before sending. `hw.calibrate()`
  does this inside `set_kb_zone()` and `set_kb_backlight()` (for effects that carry a colour).
  Never calibrate twice, and note that colours read back from the firmware are already
  calibrated.
- **Fan custom speed order.** In custom mode the speed must be written right after the mode is
  set to custom (the driver does it; `hw.set_fan` writes `pwm` before `pwm_enable`). The firmware
  returns status 2 for percentages outside 0–100.
- **The EC forgets settings** on some power transitions (resume). That is why state is saved and
  re-applied by `nitrosense-restore.service` and the `nitrosense-sleep` hook, and why
  `lightd` rewrites every zone every `REFRESH_PERIOD` seconds.
- **Windows key has no getter**: reads return `unknown` (→ `None` in `hw.get_win_key`).
- **A sleeping discrete GPU reports 0 °C**; `effects._valid()` treats ≤ 1 °C as missing.
- **Dynamic mode and firmware effects exclude each other.** Choosing a firmware effect via
  `kb` clears `dynamic.mode` (and stops the service first).
- **The mode can change behind your back** (acer-wmi or the EC); the GUI poll re-syncs the
  buttons using `_loading` to avoid writing back.
- **`state_path()`** prefers the shared system file only if it is writable, else the per-user
  file. Tests patch `state.SYSTEM_STATE`.
- **DKMS + Secure Boot.** The installer imports the DKMS signing key (MOK) when Secure Boot is
  on; the module loads only after the user enrols it on reboot. It also removes stale DKMS
  entries from an older broken version string — don't remove that loop.
- **Do not guess the firmware.** The `AN515-57` returns 0 for MUX support
  (`GetGamingMiscSetting(0x09)`), so there is no MUX switch in the driver.

## 9. Pull-request checklist

- [ ] Unit tests pass (`python3 -m unittest discover -s tests`); new behaviour has tests.
- [ ] Kernel changes build with `make -C <copy of kernel/>` and keep locking rules.
- [ ] `PROTOCOL.md` updated for any new/changed firmware call.
- [ ] New attributes added to `data/fix-permissions` and to the fake sysfs in the tests.
- [ ] `install.sh` / `uninstall.sh` still symmetric.
- [ ] README.md **and** README.es.md updated if the user-visible features changed.
- [ ] SPDX headers on new files; no Acer code/binaries; no new non-GPL-compatible assets.
- [ ] Commits in English, signed off by the human contributor, no AI trailers.
