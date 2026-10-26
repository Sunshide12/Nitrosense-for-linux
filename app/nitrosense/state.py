# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Persistent settings, the Linux counterpart of HKLM\\SOFTWARE\\OEM\\NitroSense.

The firmware forgets fan and keyboard settings on some power transitions and
cannot report everything back (fan behavior, Windows key), so the last chosen
values are stored and re-applied at boot and after resume, like PSSvc does.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile

from .hw import FANS, Hardware

SYSTEM_STATE = "/var/lib/nitrosense/state.json"
USER_STATE = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "nitrosense", "state.json"
)

DEFAULTS = {
    "operating_mode": "default",
    "fan_mode": "auto",                 # auto | max | custom
    "fans": {
        "cpu": {"auto": False, "percent": 70},
        "gpu": {"auto": False, "percent": 30},
    },
    "coolboost": False,
    "keyboard": {
        "effect": "static",
        "brightness": 100,
        "speed": 4,
        "direction": 1,
        "color": "ff0000",
        "zones": ["ff0000", "ff0000", "ff0000", "ff0000"],
        "timeout": False,
        # software-animated modes (effects.py); mode None = use the firmware effect above
        "dynamic": {"mode": None, "color1": "ff3b00", "color2": "0066ff", "speed": 5},
    },
    "win_key": True,
    "lcd_overdrive": None,              # None: leave the firmware value alone
    "temperature_unit": "C",
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif key in out:
            out[key] = value
    return out


def state_path() -> str:
    """The shared system file when we may write it, otherwise a per-user copy."""
    directory = os.path.dirname(SYSTEM_STATE)
    if os.access(SYSTEM_STATE, os.W_OK) or (
        not os.path.exists(SYSTEM_STATE) and os.access(directory, os.W_OK)
    ):
        return SYSTEM_STATE
    return USER_STATE


def load() -> dict:
    for path in (state_path(), SYSTEM_STATE):
        try:
            with open(path) as f:
                return _merge(DEFAULTS, json.load(f))
        except (OSError, ValueError):
            continue
    return copy.deepcopy(DEFAULTS)


def save(state: dict) -> str:
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state-")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(tmp, 0o664)
    os.replace(tmp, path)
    return path


def apply_fans(hw: Hardware, state: dict) -> None:
    mode = state["fan_mode"]
    for fan in FANS:
        cfg = state["fans"][fan]
        if mode == "custom":
            hw.set_fan(fan, "auto" if cfg["auto"] else "custom", cfg["percent"])
        else:
            hw.set_fan(fan, mode)


def apply_keyboard(hw: Hardware, state: dict) -> None:
    kb = state["keyboard"]
    if kb["dynamic"]["mode"]:
        # the animation loop paints the zones; the firmware only needs the static base
        hw.set_kb_backlight("static", kb["brightness"])
        return
    hw.set_kb_backlight(kb["effect"], kb["brightness"], kb["speed"], kb["direction"], kb["color"])
    if kb["effect"] == "static":
        for zone, color in enumerate(kb["zones"], start=1):
            hw.set_kb_zone(zone, color)


def apply(hw: Hardware, state: dict) -> list[str]:
    """Apply every stored setting; returns the list of errors (empty on success)."""
    errors = []
    steps = [
        ("modo de funcionamiento", lambda: hw.set_mode(state["operating_mode"])),
        ("ventiladores", lambda: apply_fans(hw, state)),
        ("CoolBoost", lambda: hw.set_coolboost(state["coolboost"])),
        ("teclado", lambda: apply_keyboard(hw, state)),
        ("apagado de retroiluminación", lambda: hw.set_kb_timeout(state["keyboard"]["timeout"])),
        ("tecla Windows", lambda: hw.set_win_key(state["win_key"])),
    ]
    if state.get("lcd_overdrive") is not None:
        steps.append(("LCD overdrive", lambda: hw.set_lcd_overdrive(state["lcd_overdrive"])))

    for name, step in steps:
        try:
            step()
        except Exception as e:  # keep applying the rest, report at the end
            errors.append(f"{name}: {e}")
    return errors
