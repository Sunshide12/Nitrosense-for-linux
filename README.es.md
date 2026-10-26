# NitroSense para Linux

[English](README.md) · **Español**

![Monitor](docs/screenshots/monitor.png)

Port a Linux (Fedora) de **Acer NitroSense** para el **Acer Nitro 5 AN515-57**: control
de ventiladores, modos de funcionamiento, CoolBoost, teclado RGB de 4 zonas y
monitorización de temperaturas.

NitroSense no se puede "recompilar" para Linux: es una aplicación WPF de .NET Framework
que depende de un servicio de Windows y de WMI. Lo que hace este proyecto es
reimplementar su función desde cero:

| Windows | Linux (este proyecto) |
|---------|------------------------|
| `PSSvc.exe` + WMI | `kernel/nitrosense.c`: driver WMI del kernel (DKMS) |
| `NitroSense.exe` | `nitrosense`: aplicación GTK4 / libadwaita |
| registro `HKLM\SOFTWARE\OEM\NitroSense` | `/var/lib/nitrosense/state.json` |
| servicio que reaplica los ajustes | `nitrosense-restore.service` + hook de suspensión |
| — | `nitrosense-ctl`: línea de comandos |

El protocolo se obtuvo por ingeniería inversa de NitroSense 3.1.3056 (IL del ejecutable
.NET, desensamblado del servicio nativo y las tablas ACPI de la BIOS). Está documentado en
[PROTOCOL.md](PROTOCOL.md). No se incluye código ni binarios de Acer.

## Funciones

| NitroSense | Linux | Notas |
|------------|-------|-------|
| Monitor de temperaturas y RPM | ✅ | también por `hwmon` estándar (`sensors`, GNOME, CoolerControl) |
| Modo Silencio / Predeterminado / Extremo / Turbo | ✅ | solo los que la BIOS anuncia como soportados |
| Ventiladores Auto / Máximo / Personalizado | ✅ | por ventilador, interfaz `pwm` estándar |
| CoolBoost | ✅ | |
| Teclado RGB: estático 4 zonas, respiración, neón, ola, desplazamiento, zoom | ✅ | con la calibración de color de NitroSense |
| **Modos dinámicos** (extra, no existen en Windows): Termómetro, Aurora, Carga de CPU, Brasas | ✅ | animados por software sobre las 4 zonas, ver abajo |
| Apagar retroiluminación tras 30 s | ✅ | |
| Bloqueo de teclas Windows/Menú | ✅ | |
| LCD Overdrive | ✅ | |
| Overclocking CPU/GPU | ❌ | el AN515-57 no lo soporta (`CPU_OC_Support = 0`) |
| Conmutador MUX de GPU | ❌ | la BIOS del AN515-57 no lo ofrece |
| NVIDIA Whisper Mode | ❌ | función del driver NVIDIA de Windows |
| Sonido DTS, macros, teclas rápidas | ❌ | funciones de Windows; en Linux: EasyEffects, input-remapper |

## Instalación en Fedora

1. Descarga el proyecto:

   ```bash
   git clone https://github.com/Sunshide12/Nitrosense-for-linux.git
   ```
2. Actualiza el sistema y reinicia si se actualizó el kernel (el módulo se compila contra
   el kernel en ejecución):

   ```bash
   sudo dnf upgrade --refresh
   ```

3. Instala:

   ```bash
   cd Nitrosense-for-linux
   sudo bash install.sh
   ```

   El instalador instala `dkms`, `kernel-devel`, GTK4 y libadwaita, compila el módulo, crea
   el grupo `nitrosense` y te añade a él.

4. **Si tienes Secure Boot activado** (lo normal), el instalador registra la clave con la
   que DKMS firma el módulo y te pide una contraseña temporal. Reinicia y en la pantalla
   azul de *MOK management* elige **Enroll MOK → Continue → Yes** e introduce esa contraseña.
   Solo hay que hacerlo una vez; las actualizaciones del kernel recompilan y firman el
   módulo automáticamente.

5. Cierra sesión y vuelve a entrar (para que el grupo `nitrosense` tenga efecto) y abre
   **NitroSense** desde el menú de aplicaciones.

Fedora Atomic (Silverblue, Kinoite) no está soportado por el instalador.

## Uso

### Aplicación

Cuatro secciones, como en NitroSense: **Monitor** (modo de funcionamiento y medidores),
**Ventiladores**, **Iluminación** y **Ajustes**. Los cambios se aplican al momento, se
guardan y se reaplican al arrancar y al volver de la suspensión.

### Línea de comandos

```bash
nitrosense-ctl status                    # temperaturas, RPM y ajustes (--json para scripts)
nitrosense-ctl mode extreme              # quiet | default | extreme | turbo
nitrosense-ctl fan auto                  # auto | max | custom
nitrosense-ctl fan custom --cpu 70 --gpu auto
nitrosense-ctl coolboost on
nitrosense-ctl kb static --brightness 100 --zones ff0000 00ff00 0000ff ffffff
nitrosense-ctl kb wave --speed 5 --direction left-to-right --brightness 75
nitrosense-ctl kb breathing --color 00ffff --speed 3
nitrosense-ctl light list                # describe los modos dinámicos
nitrosense-ctl light thermal             # thermal | aurora | load | ember
nitrosense-ctl light aurora --color1 ff00aa --color2 00ffd5 --speed 4 --brightness 75
nitrosense-ctl light off                 # volver al efecto normal del teclado
nitrosense-ctl kbtimeout on              # apagar la retroiluminación tras 30 s
nitrosense-ctl winkey off                # bloquear teclas Windows/Menú
nitrosense-ctl restore                   # reaplicar los ajustes guardados
```

### Modos dinámicos de iluminación

El firmware solo ofrece seis efectos fijos, así que estos modos pintan las 4 zonas por
software, unas 15 veces por segundo, y reaccionan al equipo:

| Modo | Qué hace |
|------|----------|
| `thermal` (Termómetro) | izquierda = temperatura de la CPU, derecha = GPU, con degradado azul → verde → ámbar → rojo; late a partir de 85 °C. Si la GPU dedicada está dormida (reporta 0 °C) usa la CPU en todo el teclado |
| `aurora` | degradado fluido entre dos colores a tu elección (`--color1`, `--color2`, `--speed`) |
| `load` (Carga de CPU) | barra que se llena zona a zona con el uso del procesador |
| `ember` (Brasas) | parpadeo de fuego en tonos cálidos (`--speed`) |

Se eligen en la pestaña **Iluminación → Modo dinámico** o con `nitrosense-ctl light`. El
bucle es un servicio de usuario (`nitrosense-lights.service`) que `light <modo>` activa y
que arranca solo al iniciar sesión; `light off` lo desactiva. Elegir un efecto normal con
`nitrosense-ctl kb …` o desde la app también lo desactiva. El brillo sigue siendo el del
firmware (0/25/50/75/100) y los colores usan la misma calibración que NitroSense.

### Interfaz del kernel

El driver es utilizable sin la aplicación. Rutas (el nombre del dispositivo puede llevar
un sufijo `-1`):

```
/sys/bus/wmi/devices/7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56*/
    operating_mode            quiet | default | extreme | turbo
    operating_mode_choices    modos soportados por la BIOS
    kb_backlight              "<efecto> <velocidad> <brillo> <dirección> <RRGGBB>"
    kb_zone1 … kb_zone4       RRGGBB (sin calibrar: el driver envía el valor tal cual)
    win_key, lcd_overdrive    0 | 1
    hwmon/hwmonN/
        temp1_input (CPU), temp2_input (GPU), temp3_input (sistema)
        fan1_input (CPU), fan2_input (GPU)
        pwm1, pwm2            0-255
        pwm1_enable, pwm2_enable   0 máximo, 1 manual, 2 automático
/sys/bus/wmi/devices/61EF69EA-865C-4BC3-A502-A0DEBA0CB531*/
    coolboost, kb_timeout     0 | 1
```

Como los ventiladores usan la interfaz `pwm` estándar de `hwmon`, herramientas como
**CoolerControl** o `fancontrol` pueden aplicar curvas de ventilador propias, algo que
NitroSense no permite.

## Desinstalación

```bash
sudo bash uninstall.sh           # --purge borra también los ajustes y el grupo
```

## Solución de problemas

| Síntoma | Solución |
|---------|----------|
| «El driver nitrosense no está cargado» | `sudo modprobe nitrosense`; si falla, `sudo dmesg \| grep -i nitrosense` |
| `modprobe: Key was rejected by service` | Secure Boot: falta registrar la clave MOK (paso 4) |
| «Modo solo lectura» en la app | cierra sesión y vuelve a entrar; comprueba `groups` incluye `nitrosense` |
| No compila tras actualizar el kernel | `sudo dnf install kernel-devel-$(uname -r)` y `sudo dkms autoinstall` |
| Un modo da error | la BIOS no lo soporta: mira `operating_mode_choices` |
| El modo dinámico no arranca | `systemctl --user status nitrosense-lights`; sin sesión gráfica puedes probarlo con `nitrosense-ctl light run` |

El módulo `acer_wmi` del kernel sigue funcionando a la vez (teclas Fn, wifi…). Si lo
cargas con `predator_v4=1`, también podrá cambiar el modo de funcionamiento; ambos
escriben el mismo registro del EC, así que la aplicación siempre muestra el valor real.

## Pruebas realizadas

- **Driver en VM**: `tests/vm/run.sh` arranca el kernel en QEMU con una SSDT que contiene
  los métodos ACPI `WMBH`, `WMAA` y `GGSI` **copiados de la BIOS de este portátil** (solo se
  emulan los registros del EC y el manejador SMI). 65 comprobaciones superadas con el
  kernel de Ubuntu 7.0 y con el kernel de **Fedora 44 (7.2.11)**, sin errores ACPI ni del
  kernel, incluida la descarga del módulo.
- **Fedora 44**: `tests/fedora/check.sh` en contenedor: los paquetes del instalador existen,
  DKMS compila contra `kernel-devel` 7.2.11, GTK 4.22 / libadwaita 1.9.
- **Python**: 36 tests (`python3 -m unittest discover -s tests`) del backend, la persistencia
  y la CLI sobre un sysfs simulado; prueba de humo de la GUI (`tests/gui_smoke.py`).

- **Hardware real**: instalado en un AN515-57 (BIOS V1.22) con Fedora 44, kernel 7.2.11 y
  Secure Boot activado. El módulo compila con DKMS, se firma con la clave MOK y carga; los
  sensores, las RPM y los modos disponibles se leen correctamente.

Las funciones que el ACPI resuelve
directamente en el EC (sensores, modos, tabla y velocidad de ventiladores, teclado RGB) se
ejecutaron con el código real de la BIOS. Las que pasan por SMI (comportamiento de
ventiladores, CoolBoost, apagado de retroiluminación, teclas Windows, overdrive) siguen
exactamente las palabras que envía NitroSense, pero el manejador SMI de la BIOS no se
puede ejecutar fuera del portátil. `re-tools/verify-windows.ps1` (como administrador, solo
lecturas) compara la decodificación con los valores reales desde Windows.

## Ingeniería inversa

En `re-tools/` están las herramientas usadas: `ildump.cs` (desensamblador de IL por
reflexión), `xref.py` (referencias a cadenas y desensamblado del servicio nativo con
Capstone), `wdg.py` (decodificador de `_WDG`) y `showm.sh`.

## Licencia

GPL-2.0-or-later. Ver [LICENSE](LICENSE). Proyecto independiente, sin relación con Acer.
La ingeniería inversa se hizo con fines de interoperabilidad.
