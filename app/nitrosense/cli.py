# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
"""nitrosense-ctl: command line control of the Acer Nitro gaming features."""

from __future__ import annotations

import argparse
import json
import re
import sys

from . import __version__, effects, lightd, state as st
from .hw import FANS, KB_DIRECTIONS, KB_EFFECTS, DriverMissing, Hardware, NoPermission

ON_OFF = {"on": True, "off": False, "1": True, "0": False}


def _color(value: str) -> str:
    value = value.lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", value):
        raise argparse.ArgumentTypeError("usa un color RRGGBB, por ejemplo ff0000")
    return value


def _percent_or_auto(value: str):
    if value == "auto":
        return "auto"
    n = int(value)
    if not 0 <= n <= 100:
        raise argparse.ArgumentTypeError("el porcentaje va de 0 a 100")
    return n


def cmd_status(hw: Hardware, args) -> int:
    s = hw.sensors()
    data = {
        "cpu_temp": s.cpu_temp, "gpu_temp": s.gpu_temp, "system_temp": s.sys_temp,
        "cpu_fan_rpm": s.cpu_fan, "gpu_fan_rpm": s.gpu_fan,
        "operating_mode": hw.get_mode(), "operating_modes": hw.mode_choices(),
        "fans": {f: vars(hw.get_fan(f)) for f in FANS},
    }
    for key, getter in (("coolboost", hw.get_coolboost), ("kb_timeout", hw.get_kb_timeout),
                        ("lcd_overdrive", hw.get_lcd_overdrive), ("win_key", hw.get_win_key)):
        try:
            data[key] = getter()
        except (OSError, DriverMissing):
            data[key] = None
    try:
        data["keyboard"] = vars(hw.get_kb_backlight())
        data["keyboard"]["zones"] = [hw.get_kb_zone(z) for z in range(1, 5)]
    except OSError:
        data["keyboard"] = None

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    def t(v):
        return "n/d" if v is None else f"{v:.0f} °C"

    print(f"CPU            {t(s.cpu_temp):>8}   ventilador {s.cpu_fan or 0:>5} RPM")
    print(f"GPU            {t(s.gpu_temp):>8}   ventilador {s.gpu_fan or 0:>5} RPM")
    print(f"Sistema        {t(s.sys_temp):>8}")
    print(f"Modo           {data['operating_mode']}  (disponibles: {' '.join(data['operating_modes'])})")
    for f in FANS:
        fan = data["fans"][f]
        print(f"Ventilador {f.upper()} {fan['mode']}" + (f" {fan['percent']} %" if fan["mode"] == "custom" else ""))
    print(f"CoolBoost      {data['coolboost']}")
    if data["keyboard"]:
        kb = data["keyboard"]
        print(f"Teclado        efecto {kb['effect']}, brillo {kb['brightness']} %, zonas {' '.join(kb['zones'])}")
        dyn = st.load()["keyboard"]["dynamic"]["mode"]
        if dyn:
            print(f"Modo dinámico  {dyn}")
    return 0


def cmd_mode(hw: Hardware, args) -> int:
    if args.mode is None:
        print(hw.get_mode())
        return 0
    hw.set_mode(args.mode)
    state = st.load()
    state["operating_mode"] = args.mode
    st.save(state)
    return 0


def cmd_fan(hw: Hardware, args) -> int:
    state = st.load()
    state["fan_mode"] = args.mode
    if args.mode == "custom":
        for fan in FANS:
            value = getattr(args, fan)
            if value == "auto":
                state["fans"][fan]["auto"] = True
            elif value is not None:
                state["fans"][fan] = {"auto": False, "percent": value}
    st.apply_fans(hw, state)
    st.save(state)
    return 0


def cmd_toggle(setter, key_path):
    def run(hw: Hardware, args) -> int:
        value = ON_OFF[args.state]
        setter(hw, value)
        state = st.load()
        target = state
        for key in key_path[:-1]:
            target = target[key]
        target[key_path[-1]] = value
        st.save(state)
        return 0
    return run


def cmd_kb(hw: Hardware, args) -> int:
    state = st.load()
    kb = state["keyboard"]
    was_dynamic = kb["dynamic"]["mode"] is not None
    kb["dynamic"]["mode"] = None
    kb["effect"] = args.effect
    for key in ("brightness", "speed", "color"):
        if getattr(args, key) is not None:
            kb[key] = getattr(args, key)
    if args.direction is not None:
        kb["direction"] = KB_DIRECTIONS[args.direction]
    if args.zones:
        zones = args.zones + [args.zones[-1]] * (4 - len(args.zones))
        kb["zones"] = zones[:4]
    if was_dynamic:
        lightd.set_dynamic(hw, state)   # stops the animation, then applies the firmware effect
    else:
        st.apply_keyboard(hw, state)
    st.save(state)
    return 0


def cmd_light(hw: Hardware, args) -> int:
    if args.mode == "run":
        return lightd.run(hw)
    if args.mode == "list":
        for mode, (label, description) in effects.MODES.items():
            print(f"{mode:<8} {label}: {description}")
        return 0
    state = st.load()
    kb = state["keyboard"]
    dyn = kb["dynamic"]
    dyn["mode"] = None if args.mode == "off" else args.mode
    for key in ("color1", "color2", "speed"):
        if getattr(args, key) is not None:
            dyn[key] = getattr(args, key)
    if args.brightness is not None:
        kb["brightness"] = args.brightness
    lightd.set_dynamic(hw, state)
    st.save(state)
    return 0


def cmd_restore(hw: Hardware, args) -> int:
    errors = st.apply(hw, st.load())
    for e in errors:
        print(f"nitrosense-ctl: {e}", file=sys.stderr)
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nitrosense-ctl", description="Control de Acer NitroSense en Linux")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="temperaturas, ventiladores y ajustes actuales")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("mode", help="ver o cambiar el modo de funcionamiento")
    s.add_argument("mode", nargs="?", choices=["quiet", "default", "extreme", "turbo"])
    s.set_defaults(func=cmd_mode)

    s = sub.add_parser("fan", help="modo de ventiladores: auto, max o custom")
    s.add_argument("mode", choices=["auto", "max", "custom"])
    s.add_argument("--cpu", type=_percent_or_auto, help="0-100 o 'auto' (solo custom)")
    s.add_argument("--gpu", type=_percent_or_auto, help="0-100 o 'auto' (solo custom)")
    s.set_defaults(func=cmd_fan)

    for name, setter, path, help_text in (
        ("coolboost", lambda hw, v: hw.set_coolboost(v), ["coolboost"], "CoolBoost on/off"),
        ("winkey", lambda hw, v: hw.set_win_key(v), ["win_key"], "teclas Windows/Menú on/off"),
        ("overdrive", lambda hw, v: hw.set_lcd_overdrive(v), ["lcd_overdrive"], "LCD overdrive on/off"),
        ("kbtimeout", lambda hw, v: hw.set_kb_timeout(v), ["keyboard", "timeout"],
         "apagar la retroiluminación tras 30 s de inactividad"),
    ):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("state", choices=["on", "off"])
        s.set_defaults(func=cmd_toggle(setter, path))

    s = sub.add_parser("kb", help="iluminación del teclado")
    s.add_argument("effect", choices=list(KB_EFFECTS))
    s.add_argument("--brightness", type=int, choices=[0, 25, 50, 75, 100])
    s.add_argument("--speed", type=int, choices=range(1, 10), metavar="1-9")
    s.add_argument("--direction", choices=list(KB_DIRECTIONS))
    s.add_argument("--color", type=_color, help="color RRGGBB del efecto")
    s.add_argument("--zones", type=_color, nargs="+", metavar="RRGGBB",
                   help="colores de las 4 zonas en modo static")
    s.set_defaults(func=cmd_kb)

    s = sub.add_parser("light", help="modos de iluminación dinámicos (animados por software)")
    s.add_argument("mode", choices=[*effects.MODES, "off", "list", "run"],
                   help="termómetro, aurora, carga, brasas; 'off' vuelve al efecto normal, "
                        "'list' los describe, 'run' ejecuta la animación en primer plano")
    s.add_argument("--color1", type=_color, help="primer color (aurora)")
    s.add_argument("--color2", type=_color, help="segundo color (aurora)")
    s.add_argument("--speed", type=int, choices=range(1, 10), metavar="1-9")
    s.add_argument("--brightness", type=int, choices=[0, 25, 50, 75, 100])
    s.set_defaults(func=cmd_light)

    s = sub.add_parser("restore", help="volver a aplicar los ajustes guardados")
    s.set_defaults(func=cmd_restore)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    hw = Hardware()
    try:
        hw.require()
        return args.func(hw, args)
    except (DriverMissing, NoPermission, RuntimeError) as e:
        print(f"nitrosense-ctl: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"nitrosense-ctl: el firmware rechazó la orden ({e.strerror or e})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
