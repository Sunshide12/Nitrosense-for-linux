"""Tests for the Python side (hw backend, persistent state, CLI) on a fake sysfs.

Run: python3 -m unittest discover -s tests -v
"""
import io
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from nitrosense import cli, effects, lightd, state as st  # noqa: E402
from nitrosense.hw import GAMING_GUID, GENERIC_GUID, Hardware, calibrate  # noqa: E402

GAMING_FILES = {
    "operating_mode": "default",
    "operating_mode_choices": "quiet default extreme turbo",
    "kb_backlight": "0 0 100 0 000000",
    "kb_zone1": "000000", "kb_zone2": "000000", "kb_zone3": "000000", "kb_zone4": "000000",
    "win_key": "unknown",
    "lcd_overdrive": "0",
}
HWMON_FILES = {
    "name": "nitrosense",
    "temp1_input": "45000", "temp2_input": "55000", "temp3_input": "32000",
    "fan1_input": "2200", "fan2_input": "2100",
    "pwm1": "128", "pwm1_enable": "2", "pwm2": "128", "pwm2_enable": "2",
}
GENERIC_FILES = {"coolboost": "0", "kb_timeout": "0"}


def make_tree(root):
    gaming = os.path.join(root, "bus/wmi/devices", GAMING_GUID)
    generic = os.path.join(root, "bus/wmi/devices", GENERIC_GUID)
    hwmon = os.path.join(gaming, "hwmon/hwmon7")
    for base, files in ((gaming, GAMING_FILES), (generic, GENERIC_FILES), (hwmon, HWMON_FILES)):
        os.makedirs(base, exist_ok=True)
        for name, value in files.items():
            with open(os.path.join(base, name), "w") as f:
                f.write(value + "\n")
    return gaming, generic, hwmon


def load_json(path):
    with open(path) as f:
        return json.load(f)


def read(base, name):
    with open(os.path.join(base, name)) as f:
        return f.read().strip()


class FakeSysfsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sysfs = os.path.join(self.tmp.name, "sys")
        self.gaming, self.generic, self.hwmon = make_tree(self.sysfs)
        self.state_file = os.path.join(self.tmp.name, "lib/state.json")
        os.makedirs(os.path.dirname(self.state_file))
        patches = [
            mock.patch.dict(os.environ, {"NITROSENSE_SYSFS": self.sysfs,
                                         "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg")}),
            mock.patch.object(st, "SYSTEM_STATE", self.state_file),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.hw = Hardware()


class HardwareTest(FakeSysfsTest):
    def test_discovery(self):
        self.assertTrue(self.hw.available)
        self.assertEqual(self.hw.hwmon, self.hwmon)

    def test_missing_driver(self):
        hw = Hardware(os.path.join(self.tmp.name, "empty"))
        self.assertFalse(hw.available)
        with self.assertRaises(Exception):
            hw.sensors()

    def test_sensors(self):
        s = self.hw.sensors()
        self.assertEqual((s.cpu_temp, s.gpu_temp, s.sys_temp), (45.0, 55.0, 32.0))
        self.assertEqual((s.cpu_fan, s.gpu_fan), (2200, 2100))

    def test_fan_custom_writes_speed_before_mode(self):
        self.hw.set_fan("gpu", "custom", 30)
        self.assertEqual(read(self.hwmon, "pwm2"), "76")      # round(30 * 255 / 100)
        self.assertEqual(read(self.hwmon, "pwm2_enable"), "1")
        self.assertEqual(self.hw.get_fan("gpu").mode, "custom")
        self.assertEqual(self.hw.get_fan("gpu").percent, 30)

    def test_fan_max_and_auto(self):
        self.hw.set_fan("cpu", "max")
        self.assertEqual(read(self.hwmon, "pwm1_enable"), "0")
        self.hw.set_fan("cpu", "auto")
        self.assertEqual(read(self.hwmon, "pwm1_enable"), "2")

    def test_calibration_matches_nitrosense(self):
        # zone_kb_adjust = {0.8, 1, 1}, Math.Floor
        self.assertEqual(calibrate("ffffff"), "ccffff")
        self.assertEqual(calibrate("0a0b0c"), "080b0c")

    def test_kb_effects(self):
        self.hw.set_kb_backlight("wave", 75, 5, 1, "ff8000")
        self.assertEqual(read(self.gaming, "kb_backlight"), "3 5 75 1 cc8000")
        self.hw.set_kb_backlight("neon", 50, 3, 2, "ff0000")   # neon: no colour, no direction
        self.assertEqual(read(self.gaming, "kb_backlight"), "2 3 50 0 000000")
        self.hw.set_kb_backlight("static", 100, 9, 2, "ffffff")
        self.assertEqual(read(self.gaming, "kb_backlight"), "0 0 100 0 000000")

    def test_toggles(self):
        self.hw.set_coolboost(True)
        self.hw.set_kb_timeout(True)
        self.hw.set_win_key(False)
        self.hw.set_lcd_overdrive(True)
        self.assertEqual(read(self.generic, "coolboost"), "1")
        self.assertEqual(read(self.generic, "kb_timeout"), "1")
        self.assertEqual(read(self.gaming, "win_key"), "0")
        self.assertEqual(read(self.gaming, "lcd_overdrive"), "1")


class StateTest(FakeSysfsTest):
    def test_defaults_and_merge(self):
        with open(self.state_file, "w") as f:
            json.dump({"fan_mode": "max", "keyboard": {"brightness": 25}, "bogus": 1}, f)
        s = st.load()
        self.assertEqual(s["fan_mode"], "max")
        self.assertEqual(s["keyboard"]["brightness"], 25)
        self.assertEqual(s["keyboard"]["effect"], "static")   # default kept
        self.assertNotIn("bogus", s)

    def test_apply_everything(self):
        s = st.load()
        s.update(operating_mode="extreme", fan_mode="custom", coolboost=True, win_key=False)
        s["fans"]["cpu"] = {"auto": True, "percent": 10}
        s["fans"]["gpu"] = {"auto": False, "percent": 100}
        s["keyboard"]["zones"] = ["ff0000", "00ff00", "0000ff", "ffffff"]
        self.assertEqual(st.apply(self.hw, s), [])
        self.assertEqual(read(self.gaming, "operating_mode"), "extreme")
        self.assertEqual(read(self.hwmon, "pwm1_enable"), "2")      # custom + auto checkbox
        self.assertEqual(read(self.hwmon, "pwm2_enable"), "1")
        self.assertEqual(read(self.hwmon, "pwm2"), "255")
        self.assertEqual(read(self.gaming, "kb_zone1"), "cc0000")
        self.assertEqual(read(self.gaming, "kb_zone4"), "ccffff")
        self.assertEqual(read(self.generic, "coolboost"), "1")
        self.assertEqual(read(self.gaming, "win_key"), "0")
        self.assertEqual(read(self.gaming, "lcd_overdrive"), "0")   # None: untouched

    def test_apply_reports_errors_and_continues(self):
        self.hw.generic = None          # APGeAction block missing on this machine
        s = st.load()
        s["operating_mode"] = "quiet"
        errors = st.apply(self.hw, s)
        self.assertEqual(len(errors), 2)
        self.assertIn("CoolBoost", errors[0])
        self.assertIn("retroiluminación", errors[1])
        self.assertEqual(read(self.gaming, "operating_mode"), "quiet")
        self.assertEqual(read(self.hwmon, "pwm1_enable"), "2")    # later steps still applied

    def test_save_roundtrip(self):
        s = st.load()
        s["coolboost"] = True
        self.assertEqual(st.save(s), self.state_file)
        self.assertTrue(st.load()["coolboost"])


class CliTest(FakeSysfsTest):
    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_status_json(self):
        code, out = self.run_cli("status", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["cpu_temp"], 45.0)
        self.assertEqual(data["operating_modes"], ["quiet", "default", "extreme", "turbo"])
        self.assertEqual(data["fans"]["cpu"]["mode"], "auto")
        self.assertEqual(data["keyboard"]["zones"], ["000000"] * 4)

    def test_status_text(self):
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("45 °C", out)

    def test_mode_and_fan(self):
        self.assertEqual(self.run_cli("mode", "turbo")[0], 0)
        self.assertEqual(read(self.gaming, "operating_mode"), "turbo")
        self.assertEqual(self.run_cli("fan", "custom", "--cpu", "60", "--gpu", "auto")[0], 0)
        self.assertEqual(read(self.hwmon, "pwm1"), "153")
        self.assertEqual(read(self.hwmon, "pwm1_enable"), "1")
        self.assertEqual(read(self.hwmon, "pwm2_enable"), "2")
        saved = load_json(self.state_file)
        self.assertEqual(saved["operating_mode"], "turbo")
        self.assertEqual(saved["fans"]["cpu"], {"auto": False, "percent": 60})

    def test_kb_and_toggles(self):
        self.assertEqual(self.run_cli("kb", "static", "--brightness", "50", "--zones", "ff0000", "00ff00")[0], 0)
        self.assertEqual(read(self.gaming, "kb_backlight"), "0 0 50 0 000000")
        self.assertEqual(read(self.gaming, "kb_zone2"), "00ff00")
        self.assertEqual(read(self.gaming, "kb_zone4"), "00ff00")   # last colour repeated
        self.assertEqual(self.run_cli("kb", "breathing", "--color", "#00FFFF", "--speed", "3")[0], 0)
        self.assertEqual(read(self.gaming, "kb_backlight"), "1 3 50 0 00ffff")
        self.assertEqual(self.run_cli("coolboost", "on")[0], 0)
        self.assertEqual(read(self.generic, "coolboost"), "1")
        self.assertEqual(self.run_cli("kbtimeout", "on")[0], 0)
        self.assertTrue(load_json(self.state_file)["keyboard"]["timeout"])

    def test_restore(self):
        self.run_cli("fan", "max")
        for name in ("pwm1_enable", "pwm2_enable"):
            with open(os.path.join(self.hwmon, name), "w") as f:
                f.write("2\n")
        self.assertEqual(self.run_cli("restore")[0], 0)
        self.assertEqual(read(self.hwmon, "pwm1_enable"), "0")
        self.assertEqual(read(self.hwmon, "pwm2_enable"), "0")

    def test_driver_missing_exit_code(self):
        with mock.patch.dict(os.environ, {"NITROSENSE_SYSFS": os.path.join(self.tmp.name, "none")}):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                self.assertEqual(cli.main(["status"]), 2)
            self.assertIn("modprobe nitrosense", err.getvalue())


def channels(color):
    return effects.to_rgb(color)


class EffectsTest(unittest.TestCase):
    def test_every_mode_renders_four_colours(self):
        ctx = effects.Context(cpu_temp=55, gpu_temp=65, cpu_load=0.4)
        for mode in effects.MODES:
            for t in (0.0, 1.3, 77.7):
                colors = effects.render(mode, t, ctx)
                self.assertEqual(len(colors), 4, mode)
                for c in colors:
                    self.assertRegex(c, r"^[0-9a-f]{6}$")

    def test_gradient_clamps_and_interpolates(self):
        stops = effects.THERMAL_STOPS
        self.assertEqual(effects.gradient(stops, -50), (0, 0x40, 0xFF))
        self.assertEqual(effects.gradient(stops, 500), (0xFF, 0, 0))
        r, g, b = effects.gradient(stops, 40)       # half-way between blue and green
        self.assertAlmostEqual(g, (0x40 + 0xD0) / 2)

    def test_thermal_cpu_cold_gpu_hot(self):
        colors = effects.render("thermal", 0, effects.Context(cpu_temp=35, gpu_temp=80))
        left, right = channels(colors[0]), channels(colors[3])
        self.assertGreater(left[2], left[0])        # cold side is bluish
        self.assertGreater(right[0], right[2])      # hot side is reddish
        self.assertEqual(len(set(colors)), 4)       # a smooth ramp, not two blocks

    def test_thermal_sleeping_gpu_falls_back_to_cpu(self):
        colors = effects.render("thermal", 0, effects.Context(cpu_temp=60, gpu_temp=0))
        self.assertEqual(len(set(colors)), 1)
        self.assertEqual(effects.render("thermal", 0, effects.Context()), ["282828"] * 4)

    def test_thermal_pulses_only_when_hot(self):
        hot = effects.Context(cpu_temp=92, gpu_temp=92)
        self.assertNotEqual(effects.render("thermal", 0.05, hot), effects.render("thermal", 0.4, hot))
        warm = effects.Context(cpu_temp=60, gpu_temp=60)
        self.assertEqual(effects.render("thermal", 0.05, warm), effects.render("thermal", 0.4, warm))

    def test_load_bar_fills_zone_by_zone(self):
        def lit(load):
            colors = effects.render("load", 0, effects.Context(cpu_load=load))
            return [max(channels(c)) > 80 for c in colors]
        self.assertEqual(lit(0.0), [False] * 4)
        self.assertEqual(lit(0.5), [True, True, False, False])
        self.assertEqual(lit(1.0), [True] * 4)

    def test_aurora_stays_between_its_colours_and_loops(self):
        ctx = effects.Context(color1="ff0000", color2="0000ff", speed=5)
        for t in (0, 0.7, 3.1, 9.9):
            for c in effects.render("aurora", t, ctx):
                r, g, b = channels(c)
                self.assertEqual(g, 0)
                self.assertAlmostEqual(r + b, 255, delta=2)
        period = 1 / (0.04 * ctx.speed)
        self.assertEqual(effects.render("aurora", 1.0, ctx), effects.render("aurora", 1.0 + period, ctx))

    def test_ember_flickers_in_warm_tones(self):
        ctx = effects.Context(speed=5)
        seen = set()
        for i in range(40):
            for c in effects.render("ember", i * 0.1, ctx):
                r, g, b = channels(c)
                self.assertGreater(r, b)
                seen.add(c)
        self.assertGreater(len(seen), 20)


class CpuLoadTest(unittest.TestCase):
    def test_busy_fraction(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stat")

            def write(user, idle):
                with open(path, "w") as f:
                    f.write(f"cpu  {user} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 2 3 4\n")
            write(100, 100)
            load = lightd.CpuLoad(path)
            write(175, 125)                     # +75 busy, +25 idle
            self.assertAlmostEqual(load.read(), 0.75)
            self.assertEqual(load.read(), 0.0)  # nothing elapsed


class DynamicLightingTest(FakeSysfsTest):
    def setUp(self):
        super().setUp()
        self.calls = []

        def fake_systemctl(*args):
            self.calls.append(args)
            return SimpleNamespace(returncode=0, stderr="")
        patch = mock.patch.object(lightd, "_systemctl", fake_systemctl)
        patch.start()
        self.addCleanup(patch.stop)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def zones(self):
        return [read(self.gaming, f"kb_zone{z}") for z in range(1, 5)]

    def test_light_command_starts_the_service_and_saves_state(self):
        self.assertEqual(self.run_cli("light", "aurora", "--color1", "#00ff00", "--speed", "3",
                                      "--brightness", "75")[0], 0)
        self.assertEqual(self.calls, [("enable", lightd.UNIT), ("reload-or-restart", lightd.UNIT)])
        saved = load_json(self.state_file)["keyboard"]
        self.assertEqual(saved["dynamic"]["mode"], "aurora")
        self.assertEqual((saved["dynamic"]["color1"], saved["dynamic"]["speed"]), ("00ff00", 3))
        self.assertEqual(read(self.gaming, "kb_backlight"), "0 0 75 0 000000")   # static base
        self.assertEqual(self.zones(), ["000000"] * 4)      # the loop paints them, not the CLI

    def test_light_off_stops_first_then_restores_the_firmware_effect(self):
        self.run_cli("kb", "static", "--zones", "ff0000", "00ff00", "0000ff", "ffffff")
        self.run_cli("light", "ember")
        self.calls.clear()
        self.assertEqual(self.run_cli("light", "off")[0], 0)
        self.assertEqual(self.calls, [("disable", "--now", lightd.UNIT)])
        self.assertIsNone(load_json(self.state_file)["keyboard"]["dynamic"]["mode"])
        self.assertEqual(read(self.gaming, "kb_zone3"), "0000ff")

    def test_firmware_effect_cancels_the_dynamic_mode(self):
        self.run_cli("light", "thermal")
        self.calls.clear()
        self.assertEqual(self.run_cli("kb", "breathing", "--color", "00ffff")[0], 0)
        self.assertEqual(self.calls, [("disable", "--now", lightd.UNIT)])
        self.assertIsNone(load_json(self.state_file)["keyboard"]["dynamic"]["mode"])
        self.assertEqual(read(self.gaming, "kb_backlight"), "1 4 100 0 00ffff")

    def test_service_failure_is_reported(self):
        lightd._systemctl = lambda *a: SimpleNamespace(returncode=1, stderr="Failed to connect to bus")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertNotEqual(cli.main(["light", "load"]), 0)
        self.assertIn("nitrosense-ctl light run", err.getvalue())

    def test_list(self):
        code, out = self.run_cli("light", "list")
        self.assertEqual(code, 0)
        for mode in effects.MODES:
            self.assertIn(mode, out)

    def test_restore_keeps_the_static_base_and_leaves_zones_to_the_loop(self):
        self.run_cli("light", "aurora")
        with open(os.path.join(self.gaming, "kb_zone1"), "w") as f:
            f.write("123456\n")
        self.assertEqual(self.run_cli("restore")[0], 0)
        self.assertEqual(read(self.gaming, "kb_zone1"), "123456")

    def test_loop_paints_the_zones_and_sets_the_base(self):
        self.run_cli("light", "aurora", "--color1", "ff0000", "--color2", "0000ff", "--brightness", "50")
        self.assertEqual(lightd.run(self.hw, fps=40, max_seconds=0.5), 0)
        self.assertEqual(read(self.gaming, "kb_backlight"), "0 0 50 0 000000")
        zones = self.zones()
        self.assertEqual(len(set(zones)), 4)                # phase offset gives each zone its own colour
        for z in zones:
            self.assertRegex(z, r"^[0-9a-f]{6}$")

    def test_sighup_reloads_the_saved_mode(self):
        self.run_cli("light", "ember")

        def switch_off():
            state = st.load()
            state["keyboard"]["dynamic"]["mode"] = None
            st.save(state)
            os.kill(os.getpid(), signal.SIGHUP)
        timer = threading.Timer(0.3, switch_off)
        timer.start()
        started = time.monotonic()
        self.assertEqual(lightd.run(self.hw, fps=40, max_seconds=5), 0)
        timer.join()
        self.assertLess(time.monotonic() - started, 2)     # left on the reload, not on the timeout

    def test_loop_exits_at_once_when_no_mode_is_set(self):
        self.assertEqual(lightd.run(self.hw, fps=40, max_seconds=5), 0)
        self.assertEqual(self.zones(), ["000000"] * 4)


if __name__ == "__main__":
    unittest.main()
