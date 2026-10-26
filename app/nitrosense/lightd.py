# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dynamic keyboard lighting: the animation loop and its systemd user service.

`nitrosense-ctl light run` is the loop (started by nitrosense-lights.service). The CLI and
the GUI only edit the saved state and (re)start or stop that service through set_dynamic().
"""

from __future__ import annotations

import signal
import subprocess
import time

from . import effects, state as st
from .hw import DriverMissing, Hardware

UNIT = "nitrosense-lights.service"
FPS = 15
SENSOR_PERIOD = 1.0     # s between temperature / load readings
REFRESH_PERIOD = 2.0    # s: rewrite every zone anyway, the EC forgets colours on resume
SMOOTHING = 0.12        # per-frame weight of a new reading, keeps the colours from jumping


class CpuLoad:
    """Fraction of busy CPU time since the previous call, from /proc/stat."""

    def __init__(self, proc_stat: str = "/proc/stat"):
        self.path = proc_stat
        self.prev = self._sample()

    def _sample(self):
        with open(self.path) as f:
            fields = [int(x) for x in f.readline().split()[1:]]
        return sum(fields) - fields[3] - fields[4], sum(fields)   # busy, total (idle + iowait)

    def read(self) -> float:
        busy, total = self._sample()
        d_busy, d_total = busy - self.prev[0], total - self.prev[1]
        self.prev = (busy, total)
        return d_busy / d_total if d_total > 0 else 0.0


def _ease(current: float | None, target: float | None) -> float | None:
    if target is None:
        return None
    if current is None:
        return target
    return current + (target - current) * SMOOTHING


def run(hw: Hardware, fps: int = FPS, max_seconds: float | None = None) -> int:
    """Animate the keyboard until SIGTERM/SIGINT (or `max_seconds`, used by the tests)."""
    hw.require()
    stop = {"now": False, "reload": True}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGHUP, lambda *_: stop.update(reload=True))

    cpu_load = CpuLoad()
    ctx = effects.Context()
    mode = None
    last: list[str | None] = [None] * effects.ZONES
    last_sensors: float | None = None
    last_full = 0.0
    target = {"cpu_temp": None, "gpu_temp": None, "cpu_load": 0.0}
    start = time.monotonic()

    while not stop["now"]:
        now = time.monotonic()
        t = now - start
        if max_seconds is not None and t >= max_seconds:
            break

        if stop["reload"]:
            stop["reload"] = False
            kb = st.load()["keyboard"]
            dyn = kb["dynamic"]
            mode = dyn["mode"] if dyn["mode"] in effects.MODES else None
            ctx.color1, ctx.color2, ctx.speed = dyn["color1"], dyn["color2"], dyn["speed"]
            last = [None] * effects.ZONES
            if mode is None:
                return 0        # nothing to animate: let the service finish
            try:
                hw.set_kb_backlight("static", kb["brightness"])
            except OSError:
                pass

        if last_sensors is None or t - last_sensors >= SENSOR_PERIOD:
            last_sensors = t
            try:
                s = hw.sensors()
                target["cpu_temp"], target["gpu_temp"] = s.cpu_temp, s.gpu_temp
            except (OSError, DriverMissing):
                pass
            target["cpu_load"] = cpu_load.read()
        ctx.cpu_temp = _ease(ctx.cpu_temp, target["cpu_temp"])
        ctx.gpu_temp = _ease(ctx.gpu_temp, target["gpu_temp"])
        ctx.cpu_load = _ease(ctx.cpu_load, target["cpu_load"]) or 0.0

        colors = effects.render(mode, t, ctx)
        refresh = t - last_full >= REFRESH_PERIOD
        if refresh:
            last_full = t
        for zone, color in enumerate(colors, start=1):
            if refresh or color != last[zone - 1]:
                try:
                    hw.set_kb_zone(zone, color)
                    last[zone - 1] = color
                except OSError:
                    pass        # e.g. mid-suspend: the next refresh retries

        time.sleep(max(0.0, 1 / fps - (time.monotonic() - now)))
    return 0


# -- service control ---------------------------------------------------------------

def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def start_service() -> None:
    """Enable the user service (autostart at login); start it, or make it reload the saved settings."""
    result = _systemctl("enable", UNIT)
    if result.returncode == 0:
        result = _systemctl("reload-or-restart", UNIT)
    if result.returncode != 0:
        raise RuntimeError(
            "No se pudo iniciar el servicio de iluminación dinámica "
            f"({result.stderr.strip() or 'systemctl falló'}). "
            "Puedes ejecutarlo a mano con: nitrosense-ctl light run"
        )


def stop_service() -> None:
    _systemctl("disable", "--now", UNIT)    # a failure here only means it was not running


def set_dynamic(hw: Hardware, state: dict) -> None:
    """Make the hardware match state['keyboard']['dynamic'] (start or stop the animation)."""
    if state["keyboard"]["dynamic"]["mode"]:
        st.apply_keyboard(hw, state)    # static base at the chosen brightness
        start_service()
    else:
        stop_service()                  # first, or the loop could repaint after the restore
        st.apply_keyboard(hw, state)    # back to the saved firmware effect / zone colours
