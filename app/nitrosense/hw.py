"""Access to the nitrosense kernel driver through sysfs.

Layout created by kernel/nitrosense.c:

  /sys/bus/wmi/devices/7A4DDFE7-.../            AcerGamingFunction
      operating_mode, operating_mode_choices
      kb_backlight, kb_zone1..4, win_key, lcd_overdrive
      hwmon/hwmonN/  temp1-3_input, fan1-2_input, pwm1-2, pwm1-2_enable
  /sys/bus/wmi/devices/61EF69EA-.../            APGeAction
      coolboost, kb_timeout
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

GAMING_GUID = "7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"
GENERIC_GUID = "61EF69EA-865C-4BC3-A502-A0DEBA0CB531"

FANS = ("cpu", "gpu")
FAN_MODES = ("auto", "max", "custom")
# hwmon pwmN_enable values (0 full speed, 1 manual, 2 automatic)
_ENABLE_FOR_MODE = {"max": 0, "custom": 1, "auto": 2}
_MODE_FOR_ENABLE = {v: k for k, v in _ENABLE_FOR_MODE.items()}

# SetGamingKBBacklight effect ids
KB_EFFECTS = {
    "static": 0,
    "breathing": 1,
    "neon": 2,
    "wave": 3,
    "shifting": 4,
    "zoom": 5,
}
KB_EFFECT_NAMES = {v: k for k, v in KB_EFFECTS.items()}
KB_EFFECTS_WITH_COLOR = {"breathing", "shifting", "zoom", "wave"}
KB_EFFECTS_WITH_DIRECTION = {"wave", "shifting"}
KB_DIRECTIONS = {"right-to-left": 1, "left-to-right": 2}

# NitroSense zone_kb_adjust: the red LEDs are calibrated down to 80 %
_COLOR_ADJUST = (0.8, 1.0, 1.0)


class DriverMissing(RuntimeError):
    """The nitrosense kernel module is not loaded or did not bind."""


class NoPermission(PermissionError):
    """The current user may not write the driver attributes."""


@dataclass
class Sensors:
    cpu_temp: float | None
    gpu_temp: float | None
    sys_temp: float | None
    cpu_fan: int | None
    gpu_fan: int | None


@dataclass
class FanState:
    mode: str      # auto | max | custom
    percent: int   # requested speed for custom mode


@dataclass
class KbBacklight:
    effect: str
    speed: int
    brightness: int
    direction: int
    color: str     # RRGGBB as written by the firmware (already calibrated)


def calibrate(color: str) -> str:
    """Apply NitroSense's per-channel LED calibration to an RRGGBB colour."""
    rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{int(c * k):02x}" for c, k in zip(rgb, _COLOR_ADJUST))


def _find_device(guid: str, probe: str, sysfs: str) -> str | None:
    for path in sorted(glob.glob(os.path.join(sysfs, "bus/wmi/devices", guid + "*"))):
        if os.path.exists(os.path.join(path, probe)):
            return path
    return None


class Hardware:
    def __init__(self, sysfs: str | None = None):
        # NITROSENSE_SYSFS lets the test-suite point at a simulated tree
        sysfs = sysfs or os.environ.get("NITROSENSE_SYSFS", "/sys")
        self.sysfs = sysfs
        self.gaming = _find_device(GAMING_GUID, "operating_mode", sysfs)
        self.generic = _find_device(GENERIC_GUID, "coolboost", sysfs)
        self.hwmon = None
        if self.gaming:
            for path in glob.glob(os.path.join(self.gaming, "hwmon", "hwmon*")):
                if _read(os.path.join(path, "name")) == "nitrosense":
                    self.hwmon = path
                    break

    @property
    def available(self) -> bool:
        return self.gaming is not None and self.hwmon is not None

    def require(self) -> None:
        if not self.available:
            raise DriverMissing(
                "El driver 'nitrosense' no está cargado. Ejecuta: sudo modprobe nitrosense"
            )

    def writable(self) -> bool:
        if not self.available:
            return False
        return os.access(os.path.join(self.gaming, "operating_mode"), os.W_OK)

    # -- low level ---------------------------------------------------------

    def _get(self, base: str | None, name: str) -> str:
        if base is None:
            raise DriverMissing("Función no disponible: falta el dispositivo WMI")
        return _read(os.path.join(base, name))

    def _set(self, base: str | None, name: str, value) -> None:
        if base is None:
            raise DriverMissing("Función no disponible: falta el dispositivo WMI")
        path = os.path.join(base, name)
        try:
            with open(path, "w") as f:
                f.write(f"{value}\n")
        except PermissionError as e:
            raise NoPermission(
                f"Sin permiso para escribir {path}. Añade tu usuario al grupo "
                "'nitrosense' (sudo usermod -aG nitrosense $USER) y vuelve a iniciar sesión."
            ) from e

    # -- monitoring ----------------------------------------------------------

    def sensors(self) -> Sensors:
        self.require()

        def num(name, scale=1.0):
            try:
                return int(self._get(self.hwmon, name)) / scale
            except (OSError, ValueError):
                return None

        cpu_fan, gpu_fan = num("fan1_input"), num("fan2_input")
        return Sensors(
            cpu_temp=num("temp1_input", 1000),
            gpu_temp=num("temp2_input", 1000),
            sys_temp=num("temp3_input", 1000),
            cpu_fan=None if cpu_fan is None else int(cpu_fan),
            gpu_fan=None if gpu_fan is None else int(gpu_fan),
        )

    # -- operating mode ------------------------------------------------------

    def mode_choices(self) -> list[str]:
        self.require()
        return self._get(self.gaming, "operating_mode_choices").split()

    def get_mode(self) -> str:
        self.require()
        return self._get(self.gaming, "operating_mode")

    def set_mode(self, mode: str) -> None:
        self.require()
        self._set(self.gaming, "operating_mode", mode)

    # -- fans ----------------------------------------------------------------

    def get_fan(self, fan: str) -> FanState:
        self.require()
        n = FANS.index(fan) + 1
        enable = int(self._get(self.hwmon, f"pwm{n}_enable"))
        pwm = int(self._get(self.hwmon, f"pwm{n}"))
        return FanState(_MODE_FOR_ENABLE.get(enable, "auto"), round(pwm * 100 / 255))

    def set_fan(self, fan: str, mode: str, percent: int | None = None) -> None:
        """Same sequence as NitroSense set_single_custom_fan_state()."""
        self.require()
        if mode not in FAN_MODES:
            raise ValueError(f"modo de ventilador no válido: {mode}")
        n = FANS.index(fan) + 1
        if percent is not None:
            percent = max(0, min(100, int(percent)))
            self._set(self.hwmon, f"pwm{n}", round(percent * 255 / 100))
        self._set(self.hwmon, f"pwm{n}_enable", _ENABLE_FOR_MODE[mode])

    # -- CoolBoost / misc ---------------------------------------------------

    def get_coolboost(self) -> bool:
        return self._get(self.generic, "coolboost") == "1"

    def set_coolboost(self, on: bool) -> None:
        self._set(self.generic, "coolboost", int(bool(on)))

    def get_kb_timeout(self) -> bool:
        return self._get(self.generic, "kb_timeout") == "1"

    def set_kb_timeout(self, on: bool) -> None:
        self._set(self.generic, "kb_timeout", int(bool(on)))

    def get_win_key(self) -> bool | None:
        self.require()
        value = self._get(self.gaming, "win_key")
        return None if value == "unknown" else value == "1"

    def set_win_key(self, enabled: bool) -> None:
        self.require()
        self._set(self.gaming, "win_key", int(bool(enabled)))

    def get_lcd_overdrive(self) -> bool:
        self.require()
        return self._get(self.gaming, "lcd_overdrive") == "1"

    def set_lcd_overdrive(self, on: bool) -> None:
        self.require()
        self._set(self.gaming, "lcd_overdrive", int(bool(on)))

    # -- keyboard ------------------------------------------------------------

    def get_kb_backlight(self) -> KbBacklight:
        self.require()
        effect, speed, brightness, direction, color = self._get(self.gaming, "kb_backlight").split()
        return KbBacklight(
            KB_EFFECT_NAMES.get(int(effect), "static"),
            int(speed), int(brightness), int(direction), color,
        )

    def set_kb_backlight(self, effect: str, brightness: int, speed: int = 0,
                         direction: int = 0, color: str = "000000") -> None:
        """Write the effect word exactly as LightingDynamicUI.Set_Dynamic_Keyboard()."""
        self.require()
        if effect not in KB_EFFECTS:
            raise ValueError(f"efecto no válido: {effect}")
        if effect == "static":
            speed, direction, color = 0, 0, "000000"
        else:
            if effect not in KB_EFFECTS_WITH_DIRECTION:
                direction = 0
            color = calibrate(color) if effect in KB_EFFECTS_WITH_COLOR else "000000"
        self._set(self.gaming, "kb_backlight",
                  f"{KB_EFFECTS[effect]} {int(speed)} {int(brightness)} {int(direction)} {color}")

    def get_kb_zone(self, zone: int) -> str:
        self.require()
        return self._get(self.gaming, f"kb_zone{zone}")

    def set_kb_zone(self, zone: int, color: str) -> None:
        self.require()
        self._set(self.gaming, f"kb_zone{zone}", calibrate(color))


def _read(path: str) -> str:
    with open(path) as f:
        return f.read().strip()
