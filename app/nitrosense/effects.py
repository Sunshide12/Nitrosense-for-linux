# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Software-animated keyboard lighting.

The firmware only offers six fixed effects, so these "dynamic modes" paint the four
keyboard zones themselves, frame by frame (see lightd.py). Everything here is pure:
render() maps a point in time plus the current sensor readings to four RRGGBB colours.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ZONES = 4

# mode id -> (label, one-line description); the GUI and the CLI both read this.
MODES = {
    "thermal": ("Termómetro", "Izquierda = CPU, derecha = GPU: del azul al rojo según la temperatura"),
    "aurora": ("Aurora", "Degradado fluido entre dos colores"),
    "load": ("Carga de CPU", "Barra que se llena según el uso del procesador"),
    "ember": ("Brasas", "Parpadeo de fuego en tonos cálidos"),
}

# temperature (°C) -> colour stops for the thermal mode
THERMAL_STOPS = (
    (30, (0x00, 0x40, 0xFF)),
    (50, (0x00, 0xD0, 0x60)),
    (70, (0xFF, 0xB0, 0x00)),
    (90, (0xFF, 0x00, 0x00)),
)
LOAD_STOPS = (
    (0.0, (0x00, 0xE0, 0x50)),
    (0.5, (0xFF, 0xC0, 0x00)),
    (1.0, (0xFF, 0x10, 0x00)),
)
EMBER_STOPS = (
    (0.0, (0x30, 0x00, 0x00)),
    (0.5, (0xFF, 0x30, 0x00)),
    (1.0, (0xFF, 0xB0, 0x20)),
)
HOT = 85          # °C: the thermal mode starts to pulse
UNLIT = 0.06      # brightness of the empty zones of the load bar


@dataclass
class Context:
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    cpu_load: float = 0.0           # 0..1
    color1: str = "ff3b00"
    color2: str = "0066ff"
    speed: int = 5                  # 1..9


def to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb) -> str:
    return "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a, b, w: float) -> tuple[float, float, float]:
    return tuple(x + (y - x) * w for x, y in zip(a, b))  # type: ignore[return-value]


def scale(rgb, k: float) -> tuple[float, float, float]:
    return tuple(c * k for c in rgb)  # type: ignore[return-value]


def gradient(stops, x: float) -> tuple[float, float, float]:
    """Colour of a piecewise-linear gradient; x is clamped to the stop range."""
    if x <= stops[0][0]:
        return tuple(float(c) for c in stops[0][1])  # type: ignore[return-value]
    for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
        if x <= x1:
            return mix(c0, c1, (x - x0) / (x1 - x0))
    return tuple(float(c) for c in stops[-1][1])  # type: ignore[return-value]


def _valid(temp: float | None) -> bool:
    # a sleeping discrete GPU reports 0 °C
    return temp is not None and temp > 1


def thermal(t: float, ctx: Context) -> list[tuple[float, float, float]]:
    cpu = ctx.cpu_temp if _valid(ctx.cpu_temp) else None
    gpu = ctx.gpu_temp if _valid(ctx.gpu_temp) else None
    if cpu is None and gpu is None:
        return [(40.0, 40.0, 40.0)] * ZONES
    cpu = cpu if cpu is not None else gpu
    gpu = gpu if gpu is not None else cpu
    pulse = 1.0
    if max(cpu, gpu) >= HOT:
        pulse = 0.65 + 0.35 * (0.5 + 0.5 * math.sin(t * 2 * math.pi * 1.5))
    out = []
    for i in range(ZONES):
        w = i / (ZONES - 1)
        out.append(scale(gradient(THERMAL_STOPS, cpu + (gpu - cpu) * w), pulse))
    return out


def aurora(t: float, ctx: Context) -> list[tuple[float, float, float]]:
    a, b = to_rgb(ctx.color1), to_rgb(ctx.color2)
    out = []
    for i in range(ZONES):
        phase = t * 0.04 * ctx.speed + i * 0.22
        out.append(mix(a, b, 0.5 - 0.5 * math.cos(2 * math.pi * phase)))
    return out


def load(t: float, ctx: Context) -> list[tuple[float, float, float]]:
    level = max(0.0, min(1.0, ctx.cpu_load)) * ZONES
    out = []
    for i in range(ZONES):
        fill = max(0.0, min(1.0, level - i))
        base = gradient(LOAD_STOPS, i / (ZONES - 1))
        out.append(scale(base, UNLIT + (1.0 - UNLIT) * fill))
    return out


def ember(t: float, ctx: Context) -> list[tuple[float, float, float]]:
    out = []
    k = 0.4 + 0.2 * ctx.speed
    for i in range(ZONES):
        # a few incommensurate sines per zone look random without needing any state
        n = (math.sin(t * k * 2.3 + i * 1.7) + math.sin(t * k * 3.7 + i * 4.1)
             + math.sin(t * k * 6.1 + i * 2.9)) / 3
        out.append(gradient(EMBER_STOPS, 0.15 + 0.85 * (0.5 + 0.5 * n)))
    return out


_RENDERERS = {"thermal": thermal, "aurora": aurora, "load": load, "ember": ember}


def render(mode: str, t: float, ctx: Context) -> list[str]:
    """Four RRGGBB colours (zone 1 .. 4) for `mode` at time `t` seconds."""
    return [to_hex(c) for c in _RENDERERS[mode](t, ctx)]
