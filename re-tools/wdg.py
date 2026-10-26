# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Decode every _WDG buffer found in decompiled ACPI .dsl files (python wdg.py file.dsl ...)."""
import re
import sys
import uuid

FLAGS = {1: "EXPENSIVE", 2: "METHOD", 4: "STRING", 8: "EVENT"}

for path in sys.argv[1:]:
    text = open(path, encoding="latin-1").read()
    for m in re.finditer(r"Name \(_WDG, Buffer \(0x[0-9A-F]+\)\s*\{(.*?)\}\)", text, re.S):
        # locate enclosing Device/Scope for context
        scope = re.findall(r"(?:Device|Scope) \(([^)]+)\)", text[: m.start()])
        data = bytes(int(b, 16) for b in re.findall(r"0x([0-9A-F]{2})\b", re.sub(r"/\*.*?\*/", "", m.group(1))))
        print(f"== {path} in {scope[-1] if scope else '?'} ({len(data)} bytes)")
        for i in range(0, len(data) - 19, 20):
            e = data[i:i + 20]
            guid = uuid.UUID(bytes_le=e[:16])
            flags = e[19]
            fl = "|".join(v for k, v in FLAGS.items() if flags & k) or "DATA"
            oid = e[16:18].decode("latin-1") if not flags & 8 else f"notify 0x{e[16]:02X}"
            print(f"  {str(guid).upper()}  {oid:12} inst={e[18]} {fl}")
