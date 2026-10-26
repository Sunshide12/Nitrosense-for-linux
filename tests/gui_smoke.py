# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
"""GUI smoke test: start the app on a fake sysfs and save a PNG of every page.

usage: python3 tests/gui_smoke.py <output dir>   (needs a Wayland/X display)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

from test_userspace import make_tree  # noqa: E402

tmp = tempfile.mkdtemp()
make_tree(os.path.join(tmp, "sys"))
os.environ["NITROSENSE_SYSFS"] = os.path.join(tmp, "sys")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")

from nitrosense import gui, lightd, state as st  # noqa: E402  (selects Gtk 4 / Adw 1)
from gi.repository import Gio, GLib, Gtk  # noqa: E402

st.SYSTEM_STATE = os.path.join(tmp, "lib", "state.json")
st.USER_STATE = os.path.join(tmp, "cfg", "nitrosense", "state.json")
out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)
errors = []
service_calls = []
# never touch the real systemd user manager from the smoke test
lightd._systemctl = lambda *a: service_calls.append(a) or type("R", (), {"returncode": 0, "stderr": ""})()


def snap(widget, path) -> bool:
    w, h = widget.get_width(), widget.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, w, h)
    node = snapshot.to_node()
    if node is None:  # page not drawn yet, try again on the next tick
        return False
    widget.get_native().get_renderer().render_texture(node, None).save_to_png(path)
    return True


DEV = os.path.join(os.environ["NITROSENSE_SYSFS"], "bus/wmi/devices", "7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56")
KB = os.path.join(DEV, "kb_backlight")
wave = {}


def run_pages(app):
    win = app.window
    win.toast = lambda text: errors.append(f"toast: {text}")
    pages = ["monitor", "fans", "lighting", "settings"]

    def step(i=[0]):
        if i[0] > 0 and not snap(win, os.path.join(out_dir, f"{pages[i[0] - 1]}.png")):
            return GLib.SOURCE_CONTINUE
        if i[0] == len(pages):
            # Exercise a few handlers end to end
            win.fan_mode_buttons["custom"].set_active(True)
            win.effect_row.set_selected(3)
            GLib.timeout_add(600, select_dynamic)
            return GLib.SOURCE_REMOVE
        win.stack.set_visible_child_name(pages[i[0]])
        i[0] += 1
        return GLib.SOURCE_CONTINUE

    def select_dynamic():
        wave["kb"] = open(KB).read().strip()    # what the firmware got for "Ola", before Aurora replaces it
        win.stack.set_visible_child_name("lighting")
        win.dynamic_row.set_selected(2)         # Aurora
        GLib.timeout_add(900, finish)
        return GLib.SOURCE_REMOVE

    def finish():
        if not snap(win, os.path.join(out_dir, "lighting-dynamic.png")):
            return GLib.SOURCE_CONTINUE
        saved = st.load()["keyboard"]["dynamic"]["mode"]
        print(f"modo dinámico guardado: {saved}; llamadas a systemctl: {service_calls}")
        if saved != "aurora" or ("enable", lightd.UNIT) not in service_calls:
            errors.append("el modo dinámico no llegó al estado / al servicio")
        if win.effect_row.get_visible() or not win.dyn_colors_row.get_visible():
            errors.append("la visibilidad de los controles no cambió con el modo dinámico")
        hwmon = os.path.join(DEV, "hwmon/hwmon7")
        enable = open(os.path.join(hwmon, "pwm1_enable")).read().strip()
        effect = wave["kb"]
        print(f"pwm1_enable tras 'Personalizado': {enable}")
        print(f"kb_backlight tras elegir 'Ola': {effect}")
        if enable != "1" or not effect.startswith("3 "):
            errors.append("los controles no llegaron al sysfs")
        app.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(900, step)


app = gui.NitroSenseApp()
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)   # do not hand over to a NitroSense that is already open
app.connect("activate", lambda a: GLib.idle_add(run_pages, a))
app.run([])
print("errores:", errors or "ninguno")
sys.exit(1 if errors else 0)
