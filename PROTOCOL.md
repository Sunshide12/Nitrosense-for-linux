# Protocolo de NitroSense (Acer Nitro 5 AN515-57)

Documento de la interfaz de firmware reconstruida por ingeniería inversa de
**Acer NitroSense 3.1.3056** (Windows) sobre un Nitro AN515-57 (placa Scala_TLS, BIOS V1.22).

## Arquitectura en Windows

```
NitroSense.exe (.NET, WPF)          ── construye cada palabra de petición
   │  named pipe "PredatorSense_service_namedpipe"
   ▼
PSSvc.exe (servicio nativo, SYSTEM) ── llama a WMI en ROOT\WMI
   │  ExecMethod(AcerGamingFunction / APGeAction)
   ▼
ACPI \_SB.PC00.WMID (PNP0C14)       ── métodos WMBH / WMAA → EC o SMI
```

El servicio comprueba el ejecutable/AppContainer del cliente de la pipe y responde
`0x36` a cualquier otro proceso, así que la pipe no es utilizable desde fuera.

### Formato de la pipe

Petición: `cmd:u16 | nargs:u8 | (size:u32 | data[size])*`
Respuesta: `nargs:u8 | (size:u32 | data[size])*`

| cmd | Nombre en el servicio | Método WMI |
|----:|-----------------------|------------|
| 9  | kSvcCmdGamingPorfileWMISetFunction | AcerGamingFunction.SetGamingProfile |
| 10 | kSvcCmdGamingPorfileWMIGetFunction | AcerGamingFunction.GetGamingProfile |
| 11 | kSvcCmdGamingLEDGroupColorSetFunction | AcerGamingFunction.SetGamingProfileSetting |
| 12 | kSvcCmdGamingLEDGroupColorGetFunction | AcerGamingFunction.GetGamingLEDColor |
| 13 | kSvcCmdGetGamingSysinfoFunction | AcerGamingFunction.GetGamingSysInfo |
| 15 | kSvcCmdGamingFanGroupBehaviorSetFunction | AcerGamingFunction.SetGamingFanBehavior |
| 16 | kSvcCmdGamingFanGroupSpeedSetFunction | AcerGamingFunction.SetGamingFanSpeed |
| 17 | kSvcCmdWMISetFunction | APGeAction.SetFunction |
| 18 | kSvcCmdWMISetLogoLED | AcerGamingFunction.SetGamingLEDColor |
| 19 | kSvcCmdWMISetLogoBehavior | AcerGamingFunction.SetGamingLEDBehavior |
| 20 | kSvcCmdWMIGetFunction | APGeAction.GetFunction |
| 27 | kSvcCmdWMISetGamingKBBacklight | AcerGamingFunction.SetGamingKBBacklight |
| 28 | kSvcCmdWMISetGamingRgbKbSetting | AcerGamingFunction.SetGamingRgbKb |
| 29 | kSvcCmdWMISetGamingLEDBehavior | AcerGamingFunction.SetGamingLEDBehavior |
| 30 | kSvcCmdSetOperationMode | SetGamingMiscSetting + SetGamingFanTable |
| 33 | kSvcCmdWMISetGamingMISCSetting | AcerGamingFunction.SetGamingMiscSetting |
| 34 | kSvcCmdWMIGetGamingMISCSetting | AcerGamingFunction.GetGamingMiscSetting |

## Bloques WMI (de `_WDG` en la SSDT)

| GUID | Clase MOF | Objeto ACPI |
|------|-----------|-------------|
| `7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56` | AcerGamingFunction | `WMBH` |
| `61EF69EA-865C-4BC3-A502-A0DEBA0CB531` | APGeAction | `WMAA` |
| `1EA0BC57-521E-461B-A7A9-5D36F141A82A` | AcerGenericMethod | `WMAE` (no usado por NitroSense) |
| `676AA15E-6A47-4D9F-A2CC-1E6D18D14026` | evento de teclas (0xBC) | gestionado por `acer_wmi` |

Cada parámetro se pasa como buffer little-endian del tamaño de su tipo MOF
(4 bytes `uint32`, 8 bytes `uint64`). Toda respuesta lleva el **estado en el byte 0**
(0 = éxito) seguido de los datos.

### Métodos de AcerGamingFunction

| Id | Método | Entrada | Implementación ACPI |
|---:|--------|---------|---------------------|
| 1  | SetGamingProfile | u64 | SMI |
| 3  | GetGamingProfile | u32 | SMI |
| 5  | GetGamingSysInfo | u32 | EC (`GGSI`) |
| 6  | SetGamingRgbKb | u32 | EC `KBnR/G/B` |
| 7  | GetGamingRgbKb | u32 | EC |
| 14 | SetGamingFanBehavior | u64 | SMI |
| 16 | SetGamingFanSpeed | u64 | EC `CPUF` / `GPUF` |
| 17 | GetGamingFanSpeed | u32 | EC |
| 18 | SetGamingFanTable | u64 | EC `FTBL` |
| 20 | SetGamingKBBacklight | u8[16] | EC `KBLE KBLS KBBP KBED KBCR KBCG KBCB KLES` |
| 21 | GetGamingKBBacklight | — | EC |
| 22 | SetGamingMiscSetting | u64 | EC (`OPMS`, `TKST`) o SMI |
| 23 | GetGamingMiscSetting | u32 | EC o SMI |

## Funciones

### Sensores — `GetGamingSysInfo(0x01 | sensor << 8)`

Respuesta: valor de 16 bits en los bytes 1-2.

| Sensor | Registro EC | Significado |
|-------:|-------------|-------------|
| 0x01 | CTML | temperatura CPU (°C) |
| 0x02 | C1FR | ventilador CPU (RPM) |
| 0x03 | SKTB | temperatura del sistema (°C) |
| 0x06 | G1FR | ventilador GPU (RPM) |
| 0x0A | SKTC | temperatura GPU (°C) |

`GetGamingSysInfo(0x02)` devuelve en el byte 5 el estado de *battery boost* (`BBMS`).

### Ventiladores

`SetGamingFanBehavior(u64)`: máscara de ventiladores en el byte 0 (CPU `0x01`, GPU `0x08`)
y un campo de 2 bits por ventilador: CPU en el bit 16, GPU en el bit 22.

| Modo | Valor | Palabra para ambos ventiladores |
|------|------:|---------------------------------|
| Automático | 1 | `0x410009` |
| Máximo | 2 | `0x820009` |
| Personalizado | 3 | `0xC30009` |

`SetGamingFanSpeed(índice | porcentaje << 8)`: índice 1 = CPU, 4 = GPU, 0-100 %
(el ACPI devuelve estado 2 si está fuera de rango). NitroSense siempre envía la
velocidad justo después de poner un ventilador en *personalizado*.

**CoolBoost** — `APGeAction.SetFunction(0x07 | estado << 16)`; lectura
`GetFunction(0x0207)`, activo si el byte 1 vale 1.

### Modo de funcionamiento

`SetGamingMiscSetting(0x0B | modo << 8)` escribe `OPMS` en el EC, ajusta el límite de
potencia del procesador (`ODV0`) y notifica a Intel DPTF y NVIDIA (`NPCF`).
`GetGamingMiscSetting(0x0A)` devuelve la máscara de modos soportados (`0x33` en el AN515-57).

| Modo | Valor | Tabla EC (`SetGamingFanTable`) |
|------|------:|------:|
| Silencio | 0 | 1 |
| Predeterminado | 1 | 2 |
| Extremo | 4 | 3 |
| Turbo | 5 | 2 |

La tabla de ventiladores la elige el servicio (`set_oc_fan_table`) tras cambiar de modo.

### Teclado RGB de 4 zonas

**Efectos** — `SetGamingKBBacklight(u8[16])`:

| Byte | Campo |
|-----:|-------|
| 0 | efecto: 0 estático, 1 respiración, 2 neón, 3 ola, 4 desplazamiento, 5 zoom |
| 1 | velocidad |
| 2 | brillo (NitroSense: `(nivel - 1) * 25`, 0-100) |
| 3 | `0x08` en el efecto ola |
| 4 | dirección (1 derecha→izquierda, 2 izquierda→derecha) |
| 5-7 | color R, G, B |

El servicio rellena el array desplazando la palabra de 64 bits módulo 64, de modo que
los bytes 8-15 repiten los 0-7 y el byte 9 vale 1; el ACPI solo lee hasta el byte 8.

**Color estático por zona** — `SetGamingRgbKb(zona | R << 8 | G << 16 | B << 24)`, zona
como máscara 1, 2, 4, 8. Lectura con `GetGamingRgbKb(zona)`.

**Calibración**: NitroSense multiplica cada color por `zone_kb_adjust = {0.8, 1.0, 1.0}`
(rojo al 80 %) antes de enviarlo.

**Apagado tras 30 s** — `APGeAction`, con `hk = BK_Hotkey_Number` (132 en este equipo):

- lectura `GetFunction(0x01 | hk << 8 | 0x80000)`: brillo en el byte 4, segundos en el 5
- escritura `SetFunction(0x02 | hk << 8 | 0x80000 | brillo << 32 | (30 si activo) << 40)`

### Otros ajustes

| Función | Escritura | Lectura |
|---------|-----------|---------|
| Teclas Windows/Menú | `SetGamingProfile(0x02 \| estado << 24)` | no existe (NitroSense usa el registro) |
| LCD overdrive | `SetGamingProfile(0x10 \| estado << 48)` | `GetGamingProfile(0)`, byte 6 |
| Conmutador MUX | `SetGamingMiscSetting(0x02 \| (1 o 2) << 8)` | soportado si `GetGamingMiscSetting(0x09)` devuelve 3 — en el AN515-57 devuelve 0 |
