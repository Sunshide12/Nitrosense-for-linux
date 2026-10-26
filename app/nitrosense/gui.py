"""NitroSense for Linux: GTK4 / libadwaita interface."""

from __future__ import annotations

import math
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import APP_ID, __version__, effects, lightd, state as st  # noqa: E402
from .hw import FANS, KB_EFFECTS_WITH_COLOR, KB_EFFECTS_WITH_DIRECTION, Hardware  # noqa: E402

ACCENT = (0.90, 0.0, 0.0)

MODE_LABELS = {
    "quiet": "Silencio",
    "default": "Predeterminado",
    "extreme": "Extremo",
    "turbo": "Turbo",
}
FAN_MODE_LABELS = {"auto": "Automático", "max": "Máximo", "custom": "Personalizado"}
EFFECTS = [
    ("static", "Estático (4 zonas)"),
    ("breathing", "Respiración"),
    ("neon", "Neón"),
    ("wave", "Ola"),
    ("shifting", "Desplazamiento"),
    ("zoom", "Zoom"),
]
DIRECTIONS = [(1, "Derecha a izquierda"), (2, "Izquierda a derecha")]
DYNAMIC = [(None, "Desactivado"), *((mode, label) for mode, (label, _) in effects.MODES.items())]

CSS = b"""
@define-color accent_bg_color #e60000;
@define-color accent_color #ff3b3b;
.gauge-card { padding: 6px; }
.zone-button { min-width: 72px; min-height: 40px; }
.mode-buttons button { padding: 8px 18px; }
"""


def rgba_to_hex(rgba: Gdk.RGBA) -> str:
    return "".join(f"{round(c * 255):02x}" for c in (rgba.red, rgba.green, rgba.blue))


def hex_to_rgba(value: str) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.parse("#" + value)
    return rgba


class Gauge(Gtk.DrawingArea):
    """Circular gauge in the style of the NitroSense monitoring page."""

    def __init__(self, title: str, maximum: float, unit: str):
        super().__init__()
        self.title, self.maximum, self.unit = title, maximum, unit
        self.value: float | None = None
        self.text = "—"
        self.set_content_width(140)
        self.set_content_height(166)
        self.set_draw_func(self._draw)

    def update(self, value: float | None, text: str | None = None) -> None:
        self.value = value
        self.text = text if text is not None else ("—" if value is None else f"{value:.0f}")
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        fg = self.get_color()
        label_h = 26
        cx, cy = width / 2, (height - label_h) / 2 + 4
        radius = min(width, height - label_h) / 2 - 8
        start, end = math.radians(135), math.radians(405)

        cr.set_line_width(10)
        cr.set_line_cap(1)  # round
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
        cr.arc(cx, cy, radius, start, end)
        cr.stroke()

        if self.value is not None:
            frac = max(0.0, min(1.0, self.value / self.maximum))
            cr.set_source_rgb(*ACCENT)
            cr.arc(cx, cy, radius, start, start + (end - start) * frac)
            cr.stroke()

        def text(markup, y):
            layout = self.create_pango_layout(None)  # inherits the desktop font
            layout.set_markup(markup, -1)
            layout.set_alignment(Pango.Alignment.CENTER)
            w, h = layout.get_pixel_size()
            cr.move_to(cx - w / 2, y - h / 2)
            PangoCairo.show_layout(cr, layout)

        cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)
        text(f"<span size='xx-large' weight='bold'>{GLib.markup_escape_text(self.text)}</span>", cy - 4)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.65)
        text(f"<span size='small'>{GLib.markup_escape_text(self.unit)}</span>", cy + 22)
        text(f"<span weight='bold'>{GLib.markup_escape_text(self.title)}</span>",
             height - label_h / 2)


class CpuUsage:
    def __init__(self):
        self.prev = None

    def read(self) -> float | None:
        try:
            with open("/proc/stat") as f:
                fields = [int(x) for x in f.readline().split()[1:]]
        except OSError:
            return None
        idle, total = fields[3] + fields[4], sum(fields)
        prev, self.prev = self.prev, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))


def scale_row(title: str, lo: int, hi: int, step: int, subtitle: str | None = None):
    row = Adw.ActionRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
    scale.set_digits(0)
    scale.set_draw_value(True)
    scale.set_value_pos(Gtk.PositionType.RIGHT)
    scale.set_hexpand(True)
    scale.set_size_request(260, -1)
    scale.set_valign(Gtk.Align.CENTER)
    row.add_suffix(scale)
    return row, scale


def toggle_group(options: list[tuple[str, str]], on_toggle) -> tuple[Gtk.Box, dict]:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
    box.add_css_class("linked")
    box.add_css_class("mode-buttons")
    buttons, first = {}, None
    for key, label in options:
        button = Gtk.ToggleButton(label=label)
        if first is None:
            first = button
        else:
            button.set_group(first)
        button.connect("toggled", lambda b, k=key: b.get_active() and on_toggle(k))
        box.append(button)
        buttons[key] = button
    return box, buttons


class NitroSenseWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="NitroSense")
        self.set_default_size(980, 720)
        self.hw = Hardware()
        self.state = st.load()
        self.cpu_usage = CpuUsage()
        self._loading = False
        self._debounce: dict[str, int] = {}

        self.toasts = Adw.ToastOverlay()
        self.banner = Adw.Banner()
        self.stack = Adw.ViewStack()

        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        menu = Gio.Menu()
        menu.append("Reaplicar ajustes guardados", "app.restore")
        menu.append("Acerca de NitroSense", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(self.stack)
        self.stack.set_vexpand(True)
        self.toasts.set_child(content)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.toasts)
        self.set_content(view)

        self._build_monitor()
        self._build_fans()
        self._build_lighting()
        self._build_settings()
        self._check_driver()
        self._load_from_state()

        self._refresh()
        GLib.timeout_add_seconds(2, self._refresh)

    # -- helpers ---------------------------------------------------------

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def do(self, action, *args) -> bool:
        """Run a hardware action, save the state and report failures as a toast."""
        if self._loading:
            return False
        try:
            action(*args)
        except Exception as e:  # report driver/firmware errors without crashing the UI
            self.toast(str(e))
            return False
        try:
            st.save(self.state)
        except OSError as e:
            self.toast(f"No se pudo guardar la configuración: {e}")
        return True

    def debounce(self, key: str, fn, delay: int = 350) -> None:
        if self._loading:
            return
        if key in self._debounce:
            GLib.source_remove(self._debounce[key])

        def fire():
            self._debounce.pop(key, None)
            fn()
            return GLib.SOURCE_REMOVE

        self._debounce[key] = GLib.timeout_add(delay, fire)

    def _check_driver(self) -> None:
        if not self.hw.available:
            self.banner.set_title("El driver nitrosense no está cargado. Ejecuta «sudo modprobe nitrosense».")
            self.banner.set_revealed(True)
        elif not self.hw.writable():
            self.banner.set_title("Modo solo lectura: añade tu usuario al grupo «nitrosense» y reinicia la sesión.")
            self.banner.set_revealed(True)

    def _page(self, name: str, title: str, icon: str) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        self.stack.add_titled_with_icon(page, name, title, icon)
        return page

    # -- monitor -----------------------------------------------------------

    def _build_monitor(self) -> None:
        page = self._page("monitor", "Monitor", "computer-symbolic")

        group = Adw.PreferencesGroup(
            title="Modo de funcionamiento",
            description="Igual que NitroSense: ajusta el límite de potencia y la tabla de ventiladores del EC.",
        )
        try:
            choices = self.hw.mode_choices() if self.hw.available else list(MODE_LABELS)
        except OSError:
            choices = list(MODE_LABELS)
        box, self.mode_buttons = toggle_group(
            [(m, MODE_LABELS.get(m, m)) for m in choices], self._on_mode)
        box.set_margin_top(6)
        group.add(box)
        page.add(group)

        group = Adw.PreferencesGroup(title="Estado del sistema")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=2, max_children_per_line=3,
                           column_spacing=12, row_spacing=12)
        self.gauges = {
            "cpu_temp": Gauge("CPU", 100, "°C"),
            "gpu_temp": Gauge("GPU", 100, "°C"),
            "sys_temp": Gauge("Sistema", 100, "°C"),
            "cpu_fan": Gauge("Ventilador CPU", 7000, "RPM"),
            "gpu_fan": Gauge("Ventilador GPU", 7000, "RPM"),
            "cpu_load": Gauge("Uso de CPU", 100, "%"),
        }
        for gauge in self.gauges.values():
            frame = Gtk.Frame(child=gauge)
            frame.add_css_class("card")
            frame.add_css_class("gauge-card")
            flow.append(frame)
        group.add(flow)
        page.add(group)

    def _on_mode(self, mode: str) -> None:
        def apply():
            self.hw.set_mode(mode)
            self.state["operating_mode"] = mode
        self.do(apply)

    def _refresh(self) -> bool:
        unit = self.state["temperature_unit"]
        load = self.cpu_usage.read()
        self.gauges["cpu_load"].update(load)
        if not self.hw.available:
            return GLib.SOURCE_CONTINUE
        try:
            s = self.hw.sensors()
        except OSError:
            return GLib.SOURCE_CONTINUE
        for key in ("cpu_temp", "gpu_temp", "sys_temp"):
            value = getattr(s, key)
            gauge = self.gauges[key]
            gauge.unit = "°F" if unit == "F" else "°C"
            shown = None if value is None else (value * 9 / 5 + 32 if unit == "F" else value)
            gauge.maximum = 212 if unit == "F" else 100
            gauge.update(shown)
        self.gauges["cpu_fan"].update(s.cpu_fan)
        self.gauges["gpu_fan"].update(s.gpu_fan)

        # The mode can also change through acer-wmi or the EC itself
        try:
            mode = self.hw.get_mode()
        except OSError:
            mode = None
        if mode in self.mode_buttons and not self.mode_buttons[mode].get_active():
            self._loading = True
            self.mode_buttons[mode].set_active(True)
            self._loading = False
        return GLib.SOURCE_CONTINUE

    # -- fans ----------------------------------------------------------------

    def _build_fans(self) -> None:
        page = self._page("fans", "Ventiladores", "weather-windy-symbolic")

        group = Adw.PreferencesGroup(title="Control de ventiladores")
        box, self.fan_mode_buttons = toggle_group(list(FAN_MODE_LABELS.items()), self._on_fan_mode)
        box.set_margin_top(6)
        group.add(box)
        page.add(group)

        self.custom_group = Adw.PreferencesGroup(
            title="Personalizado",
            description="Velocidad fija para cada ventilador o control automático individual.",
        )
        self.fan_rows = {}
        for fan in FANS:
            auto = Adw.SwitchRow(title=f"Ventilador {fan.upper()} automático")
            row, scale = scale_row(f"Velocidad {fan.upper()}", 0, 100, 1, "Porcentaje")
            for v in (0, 25, 50, 75, 100):
                scale.add_mark(v, Gtk.PositionType.BOTTOM, None)
            auto.connect("notify::active", lambda *_, f=fan: self._on_fan_custom(f))
            scale.connect("value-changed", lambda *_, f=fan: self.debounce(f"fan-{f}", lambda: self._on_fan_custom(f)))
            self.custom_group.add(auto)
            self.custom_group.add(row)
            self.fan_rows[fan] = (auto, row, scale)
        page.add(self.custom_group)

        group = Adw.PreferencesGroup()
        self.coolboost = Adw.SwitchRow(
            title="CoolBoost",
            subtitle="Aumenta la velocidad de los ventiladores en modo automático",
        )
        self.coolboost.connect("notify::active", self._on_coolboost)
        group.add(self.coolboost)
        page.add(group)

    def _sync_fan_sensitivity(self) -> None:
        custom = self.state["fan_mode"] == "custom"
        self.custom_group.set_sensitive(custom)
        for fan, (auto, row, _scale) in self.fan_rows.items():
            row.set_sensitive(not auto.get_active())

    def _on_fan_mode(self, mode: str) -> None:
        if self._loading:
            return
        self.state["fan_mode"] = mode
        self._sync_fan_sensitivity()
        self.do(st.apply_fans, self.hw, self.state)

    def _on_fan_custom(self, fan: str) -> None:
        if self._loading:
            return
        auto, _row, scale = self.fan_rows[fan]
        self.state["fans"][fan] = {"auto": auto.get_active(), "percent": int(scale.get_value())}
        self._sync_fan_sensitivity()
        if self.state["fan_mode"] == "custom":
            self.do(st.apply_fans, self.hw, self.state)

    def _on_coolboost(self, row, _pspec) -> None:
        def apply():
            self.hw.set_coolboost(row.get_active())
            self.state["coolboost"] = row.get_active()
        self.do(apply)

    # -- lighting ------------------------------------------------------------

    def _build_lighting(self) -> None:
        page = self._page("lighting", "Iluminación", "keyboard-brightness-symbolic")

        group = Adw.PreferencesGroup(
            title="Modo dinámico",
            description="Animaciones por software que reaccionan al equipo. Sustituyen al efecto del "
                        "teclado mientras estén activas.")
        self.dynamic_row = Adw.ComboRow(title="Modo", model=Gtk.StringList.new([d[1] for d in DYNAMIC]))
        self.dynamic_row.connect("notify::selected", lambda *_: self._on_dynamic())
        group.add(self.dynamic_row)

        self.dyn_speed_row, self.dyn_speed = scale_row("Velocidad", 1, 9, 1)
        self.dyn_speed.connect("value-changed", lambda *_: self.debounce("dyn", self._on_dynamic, 500))
        group.add(self.dyn_speed_row)

        self.dyn_colors_row = Adw.ActionRow(title="Colores del degradado")
        self.dyn_colors = []
        for _ in range(2):
            button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
            button.connect("notify::rgba", lambda *_: self.debounce("dyn", self._on_dynamic, 500))
            self.dyn_colors_row.add_suffix(button)
            self.dyn_colors.append(button)
        group.add(self.dyn_colors_row)
        page.add(group)

        group = Adw.PreferencesGroup(title="Teclado RGB")
        self.effect_row = Adw.ComboRow(title="Efecto", model=Gtk.StringList.new([e[1] for e in EFFECTS]))
        self.effect_row.connect("notify::selected", lambda *_: self._on_keyboard())
        group.add(self.effect_row)

        row, self.brightness = scale_row("Brillo", 0, 100, 25)
        for v in (0, 25, 50, 75, 100):
            self.brightness.add_mark(v, Gtk.PositionType.BOTTOM, None)
        self.brightness.connect("value-changed", self._snap_brightness)
        group.add(row)

        self.speed_row, self.speed = scale_row("Velocidad", 1, 9, 1)
        self.speed.connect("value-changed", lambda *_: self.debounce("kb", self._on_keyboard))
        group.add(self.speed_row)

        self.direction_row = Adw.ComboRow(title="Dirección", model=Gtk.StringList.new([d[1] for d in DIRECTIONS]))
        self.direction_row.connect("notify::selected", lambda *_: self._on_keyboard())
        group.add(self.direction_row)

        self.color_row = Adw.ActionRow(title="Color del efecto")
        self.effect_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                  valign=Gtk.Align.CENTER)
        self.effect_color.connect("notify::rgba", lambda *_: self._on_keyboard())
        self.color_row.add_suffix(self.effect_color)
        group.add(self.color_row)
        page.add(group)

        self.zones_group = Adw.PreferencesGroup(
            title="Zonas", description="Color de cada una de las 4 zonas (de izquierda a derecha).")
        zones = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, homogeneous=True,
                        margin_top=6, margin_bottom=6)
        self.zone_buttons = []
        for i in range(4):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
            button.add_css_class("zone-button")
            button.connect("notify::rgba", lambda *_: self.debounce("zones", self._on_keyboard, 150))
            box.append(button)
            box.append(Gtk.Label(label=f"Zona {i + 1}", css_classes=["dim-label", "caption"]))
            zones.append(box)
            self.zone_buttons.append(button)
        self.zones_group.add(zones)
        same_button = Gtk.Button(label="Color de la zona 1 en todas", valign=Gtk.Align.CENTER,
                                 css_classes=["flat"])
        same_button.connect("clicked", self._on_same_color)
        self.zones_group.set_header_suffix(same_button)
        page.add(self.zones_group)

        group = Adw.PreferencesGroup()
        self.kb_timeout = Adw.SwitchRow(
            title="Apagar retroiluminación tras 30 s",
            subtitle="Se apaga si no se usa el teclado; vuelve al pulsar una tecla",
        )
        self.kb_timeout.connect("notify::active", self._on_kb_timeout)
        group.add(self.kb_timeout)
        page.add(group)

    def _snap_brightness(self, scale) -> None:
        snapped = round(scale.get_value() / 25) * 25
        if snapped != scale.get_value():
            scale.set_value(snapped)
            return
        self.debounce("kb", self._on_keyboard)

    def _sync_lighting_visibility(self) -> None:
        effect = EFFECTS[self.effect_row.get_selected()][0]
        dynamic = DYNAMIC[self.dynamic_row.get_selected()][0]
        # while a dynamic mode runs, only the brightness of the firmware controls still applies
        self.effect_row.set_visible(dynamic is None)
        self.speed_row.set_visible(dynamic is None and effect != "static")
        self.direction_row.set_visible(dynamic is None and effect in KB_EFFECTS_WITH_DIRECTION)
        self.color_row.set_visible(dynamic is None and effect in KB_EFFECTS_WITH_COLOR)
        self.zones_group.set_visible(dynamic is None and effect == "static")
        self.dyn_speed_row.set_visible(dynamic in ("aurora", "ember"))
        self.dyn_colors_row.set_visible(dynamic == "aurora")
        description = effects.MODES[dynamic][1] if dynamic else "Elige una animación para el teclado."
        self.dynamic_row.set_subtitle(description)

    def _on_keyboard(self) -> None:
        if self._loading:
            return
        self._sync_lighting_visibility()
        kb = self.state["keyboard"]
        kb["effect"] = EFFECTS[self.effect_row.get_selected()][0]
        kb["brightness"] = int(self.brightness.get_value())
        kb["speed"] = int(self.speed.get_value())
        kb["direction"] = DIRECTIONS[self.direction_row.get_selected()][0]
        kb["color"] = rgba_to_hex(self.effect_color.get_rgba())
        kb["zones"] = [rgba_to_hex(b.get_rgba()) for b in self.zone_buttons]
        # with a dynamic mode on, set_dynamic() also makes the running loop repaint the zones
        self.do(lightd.set_dynamic if kb["dynamic"]["mode"] else st.apply_keyboard, self.hw, self.state)

    def _on_dynamic(self) -> None:
        if self._loading:
            return
        dyn = self.state["keyboard"]["dynamic"]
        dyn["mode"] = DYNAMIC[self.dynamic_row.get_selected()][0]
        dyn["speed"] = int(self.dyn_speed.get_value())
        dyn["color1"], dyn["color2"] = (rgba_to_hex(b.get_rgba()) for b in self.dyn_colors)
        self._sync_lighting_visibility()
        self.do(lightd.set_dynamic, self.hw, self.state)

    def _on_same_color(self, _button) -> None:
        color = self.zone_buttons[0].get_rgba()
        self._loading = True
        for button in self.zone_buttons[1:]:
            button.set_rgba(color)
        self._loading = False
        self._on_keyboard()

    def _on_kb_timeout(self, row, _pspec) -> None:
        def apply():
            self.hw.set_kb_timeout(row.get_active())
            self.state["keyboard"]["timeout"] = row.get_active()
        self.do(apply)

    # -- settings ------------------------------------------------------------

    def _build_settings(self) -> None:
        page = self._page("settings", "Ajustes", "emblem-system-symbolic")

        group = Adw.PreferencesGroup(title="Teclado y pantalla")
        self.win_key = Adw.SwitchRow(
            title="Teclas Windows y Menú",
            subtitle="Desactívalas para que no interrumpan los juegos",
        )
        self.win_key.connect("notify::active", self._on_win_key)
        group.add(self.win_key)

        self.overdrive = Adw.SwitchRow(
            title="LCD Overdrive",
            subtitle="Reduce el tiempo de respuesta del panel (puede producir halos)",
        )
        self.overdrive.connect("notify::active", self._on_overdrive)
        group.add(self.overdrive)
        page.add(group)

        group = Adw.PreferencesGroup(title="Preferencias")
        self.unit_row = Adw.ComboRow(title="Unidad de temperatura",
                                     model=Gtk.StringList.new(["Celsius (°C)", "Fahrenheit (°F)"]))
        self.unit_row.connect("notify::selected", self._on_unit)
        group.add(self.unit_row)

        info = Adw.ActionRow(
            title="Restauración automática",
            subtitle="Los ajustes se guardan y el servicio nitrosense-restore los aplica "
                     "al arrancar y al volver de suspensión.",
        )
        group.add(info)
        page.add(group)

    def _on_win_key(self, row, _pspec) -> None:
        def apply():
            self.hw.set_win_key(row.get_active())
            self.state["win_key"] = row.get_active()
        self.do(apply)

    def _on_overdrive(self, row, _pspec) -> None:
        def apply():
            self.hw.set_lcd_overdrive(row.get_active())
            self.state["lcd_overdrive"] = row.get_active()
        self.do(apply)

    def _on_unit(self, row, _pspec) -> None:
        self.state["temperature_unit"] = "F" if row.get_selected() == 1 else "C"
        if not self._loading:
            try:
                st.save(self.state)
            except OSError:
                pass
            self._refresh()

    # -- initial state ---------------------------------------------------------

    def _load_from_state(self) -> None:
        s = self.state
        self._loading = True
        try:
            mode = s["operating_mode"]
            if self.hw.available:
                try:
                    mode = self.hw.get_mode()
                except OSError:
                    pass
            if mode in self.mode_buttons:
                self.mode_buttons[mode].set_active(True)

            self.fan_mode_buttons[s["fan_mode"]].set_active(True)
            for fan, (auto, _row, scale) in self.fan_rows.items():
                auto.set_active(s["fans"][fan]["auto"])
                scale.set_value(s["fans"][fan]["percent"])
            self._sync_fan_sensitivity()

            kb = s["keyboard"]
            self.effect_row.set_selected(next(i for i, e in enumerate(EFFECTS) if e[0] == kb["effect"]))
            dyn = kb["dynamic"]
            self.dynamic_row.set_selected(next((i for i, d in enumerate(DYNAMIC) if d[0] == dyn["mode"]), 0))
            self.dyn_speed.set_value(dyn["speed"])
            self.dyn_colors[0].set_rgba(hex_to_rgba(dyn["color1"]))
            self.dyn_colors[1].set_rgba(hex_to_rgba(dyn["color2"]))
            self.brightness.set_value(kb["brightness"])
            self.speed.set_value(kb["speed"])
            self.direction_row.set_selected(next((i for i, d in enumerate(DIRECTIONS) if d[0] == kb["direction"]), 0))
            self.effect_color.set_rgba(hex_to_rgba(kb["color"]))
            for button, color in zip(self.zone_buttons, kb["zones"]):
                button.set_rgba(hex_to_rgba(color))
            self._sync_lighting_visibility()

            def hw_bool(getter, fallback):
                if not self.hw.available:
                    return fallback
                try:
                    value = getter()
                except OSError:
                    return fallback
                return fallback if value is None else value

            self.coolboost.set_active(hw_bool(self.hw.get_coolboost, s["coolboost"]))
            self.kb_timeout.set_active(hw_bool(self.hw.get_kb_timeout, kb["timeout"]))
            self.win_key.set_active(hw_bool(self.hw.get_win_key, s["win_key"]))
            self.overdrive.set_active(hw_bool(self.hw.get_lcd_overdrive, bool(s["lcd_overdrive"])))
            self.unit_row.set_selected(1 if s["temperature_unit"] == "F" else 0)
        finally:
            self._loading = False


class NitroSenseApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        for name, callback in (("about", self._on_about), ("restore", self._on_restore),
                               ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        if self.window is None:
            self.window = NitroSenseWindow(self)
        self.window.present()

    def _on_restore(self, *_):
        errors = st.apply(self.window.hw, self.window.state)
        self.window.toast("Ajustes aplicados" if not errors else errors[0])

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name="NitroSense para Linux",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Port de Acer NitroSense 3.1",
            comments="Control de ventiladores, modos de funcionamiento y teclado RGB para "
                     "portátiles Acer Nitro, a partir de la ingeniería inversa de NitroSense.",
            license_type=Gtk.License.GPL_2_0,
        )
        about.present(self.window)


def main() -> int:
    return NitroSenseApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
