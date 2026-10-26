# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find functions in a PE x64 binary that reference a string, and disassemble them.

usage: xref.py <pe> <string> [max_insns]
Strings are searched as UTF-16LE and ASCII. Functions are bounded with .pdata.
"""
import bisect
import struct
import sys

import capstone
import pefile

pe_path, needle = sys.argv[1], sys.argv[2]
max_insns = int(sys.argv[3]) if len(sys.argv) > 3 else 600
pe = pefile.PE(pe_path)
base = pe.OPTIONAL_HEADER.ImageBase
data = pe.get_memory_mapped_image()

def find_all(blob, pat):
    i = blob.find(pat)
    while i != -1:
        yield i
        i = blob.find(pat, i + 1)

targets = set()
for enc in (needle.encode("utf-16le") + b"\0\0", needle.encode() + b"\0"):
    targets.update(find_all(data, enc))

# .pdata function table
funcs = []
for s in pe.sections:
    if s.Name.startswith(b".pdata"):
        raw = s.get_data()
        for off in range(0, len(raw) - 11, 12):
            b, e, _ = struct.unpack_from("<III", raw, off)
            if b and e > b:
                funcs.append((b, e))
funcs.sort()
starts = [f[0] for f in funcs]

text = next(s for s in pe.sections if s.Name.startswith(b".text"))
tbeg, tend = text.VirtualAddress, text.VirtualAddress + text.Misc_VirtualSize

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = False

def strat(rva):
    if not (0 < rva < len(data)):
        return None
    chunk = data[rva:rva + 200]
    try:
        if len(chunk) > 3 and chunk[1] == 0 and 32 <= chunk[0] < 127:
            s = chunk.decode("utf-16le", "ignore").split("\0")[0]
        else:
            s = chunk.split(b"\0")[0].decode("latin-1")
        if len(s) >= 3 and all(32 <= ord(c) < 127 for c in s):
            return s
    except Exception:
        pass
    return None

# scan for RIP-relative references (lea/mov with disp32)
code = data[tbeg:tend]
hits = set()
for i in range(len(code) - 7):
    if code[i] in (0x48, 0x4C) and code[i + 1] == 0x8D and (code[i + 2] & 0xC7) == 0x05:
        disp = struct.unpack_from("<i", code, i + 3)[0]
        tgt = tbeg + i + 7 + disp
        if tgt in targets:
            hits.add(tbeg + i)

done = set()
for h in sorted(hits):
    k = bisect.bisect_right(starts, h) - 1
    fb, fe = funcs[k] if k >= 0 and funcs[k][0] <= h < funcs[k][1] else (h - 0x40, h + 0x400)
    if fb in done:
        continue
    done.add(fb)
    print(f"\n===== function {base + fb:#x}..{base + fe:#x} (ref at {base + h:#x})")
    n = 0
    for ins in md.disasm(data[fb:fe], base + fb):
        note = ""
        if "rip" in ins.op_str and "[rip" in ins.op_str:
            try:
                d = int(ins.op_str.split("[rip ")[1].split("]")[0].replace(" ", ""), 16)
                tgt = ins.address + ins.size + d - base
                s = strat(tgt)
                note = f"   ; {s!r}" if s else f"   ; [{base + tgt:#x}]"
            except Exception:
                pass
        print(f"{ins.address:#x}: {ins.mnemonic:6} {ins.op_str}{note}")
        n += 1
        if n >= max_insns:
            print("...")
            break
