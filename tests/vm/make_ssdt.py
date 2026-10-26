"""Build acer-sim.asl: an SSDT that emulates the AN515-57 gaming WMI firmware.

The WMBH, WMAA and GGSI methods are copied verbatim from the machine's real
SSDT (decompiled with iasl). Only what lives outside ACPI is emulated:
EC registers become Names, the SMI handler (WSMI) is written in ASL, and a few
sensors are computed from the EC state so writes can be verified from Linux:

  CPU/GPU fan RPM = 2200 auto, 5800 max, percent*58 custom
  system temp     = 30 + fan table (FTBL)

usage: make_ssdt.py <real-SSDB.dsl> <out.asl>
"""
import re
import sys

src = open(sys.argv[1], encoding="latin-1").read()


def method(name: str) -> str:
    start = src.index(f"Method ({name},")
    depth, i = 0, src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


def guid_bytes(guid: str) -> list[int]:
    import uuid
    return list(uuid.UUID(guid).bytes_le)


wdg = []
for guid, oid in (("61EF69EA-865C-4BC3-A502-A0DEBA0CB531", "AA"),
                  ("7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56", "BH")):
    wdg += guid_bytes(guid) + [ord(oid[0]), ord(oid[1]), 0x01, 0x02]
wdg_text = ", ".join(f"0x{b:02X}" for b in wdg)

ec_names = ["PSEE", "KB1R", "KB1G", "KB1B", "KB2R", "KB2G", "KB2B", "KB3R", "KB3G", "KB3B",
            "KB4R", "KB4G", "KB4B", "KBLE", "KBLS", "KBBP", "KBED", "KBCR", "KBCG", "KBCB",
            "KLES", "TKST", "BBMS"]

asl = f"""
DefinitionBlock ("", "SSDT", 2, "NSTEST", "ACERSIM", 0x00000001)
{{
    Name (\\ODV0, One)
    Name (\\ODV1, One)

    Scope (\\_SB)
    {{
        Device (IETM)
        {{
            Name (_HID, "INTC1041")
            Method (ODVP, 0, NotSerialized) {{ }}
        }}
        Device (NPCF)
        {{
            Name (_HID, "NVDA0820")
        }}

        Device (PC00)
        {{
            Name (_HID, "ACPI0004")

            Device (LPCB)
            {{
                Name (_ADR, 0x001F0000)

                Device (EC0)
                {{
                    Name (_ADR, Zero)
{chr(10).join(f"                    Name ({n}, Zero)" for n in ec_names)}
                    Name (OPMS, One)
                    Name (FTBL, 0x02)
                    Name (CTML, 0x2D)
                    Name (FBCP, One)
                    Name (FBGP, One)
                    Name (CPUF, 0x32)
                    Name (GPUF, 0x32)

                    Method (FRPM, 2, NotSerialized)
                    {{
                        If ((Arg0 == 0x02)) {{ Return (5800) }}
                        If ((Arg0 == 0x03)) {{ Return ((Arg1 * 58)) }}
                        Return (2200)
                    }}
                    Method (C1FR, 0, NotSerialized) {{ Return (FRPM (FBCP, CPUF)) }}
                    Method (G1FR, 0, NotSerialized) {{ Return (FRPM (FBGP, GPUF)) }}
                    Method (SKTB, 0, NotSerialized) {{ Return ((0x1E + FTBL)) }}
                    Method (SKTC, 0, NotSerialized) {{ Return (0x37) }}

                    {method("GGSI")}
                }}
            }}

            Device (WMID)
            {{
                Name (_HID, "PNP0C14")
                Name (_UID, "APGe")
                Name (_WDG, Buffer () {{ {wdg_text} }})

                Name (WMIB, Buffer (0x0200) {{ }})
                Name (CTID, Zero)
                Name (MTID, Zero)
                Name (AAST, Buffer (0x04) {{ }})
                Name (AAGT, Buffer (0x08) {{ }})
                Name (BHSK, Buffer (0x04) {{ }})
                Name (BHGK, Buffer (0x08) {{ }})
                Name (BHLK, Buffer (0x10) {{ }})
                Name (BHPK, Buffer (0x10) {{ }})

                /* Emulated BIOS state behind the SMI handler */
                Name (PWIN, Zero)
                Name (POVD, Zero)
                Name (CBST, Zero)
                Name (KBBR, 0x64)
                Name (KBTO, Zero)

                /*
                 * Emulated SMI handler: WMIB holds the input on entry and the reply on
                 * exit. Byte-wise on purpose: QEMU's DSDT is revision 1, which makes
                 * ACPICA use 32-bit integers for the whole namespace.
                 */
                Method (WSMI, 2, Serialized)
                {{
                    Name (OUTB, Buffer (0x10) {{ }})
                    MTID = Arg0
                    WMIB = Arg1
                    Local0 = DerefOf (WMIB [Zero])
                    Local1 = DerefOf (WMIB [One])
                    Local2 = DerefOf (WMIB [0x02])
                    Local3 = Zero

                    If ((CTID == 0x4842))
                    {{
                        If ((Arg0 == One))
                        {{
                            If ((Local0 == 0x02)) {{ PWIN = DerefOf (WMIB [0x03]) }}
                            ElseIf ((Local0 == 0x10)) {{ POVD = DerefOf (WMIB [0x06]) }}
                            Else {{ Local3 = 0x03 }}
                        }}
                        ElseIf ((Arg0 == 0x03))
                        {{
                            OUTB [0x03] = PWIN
                            OUTB [0x06] = POVD
                        }}
                        ElseIf ((Arg0 == 0x0E))
                        {{
                            If ((Local0 & One)) {{ \\_SB.PC00.LPCB.EC0.FBCP = (Local2 & 0x03) }}
                            If ((Local0 & 0x08)) {{ \\_SB.PC00.LPCB.EC0.FBGP = ((Local2 >> 0x06) & 0x03) }}
                        }}
                    }}
                    ElseIf ((CTID == 0x4141))
                    {{
                        If ((Arg0 == One))
                        {{
                            If ((Local0 == 0x07)) {{ CBST = Local2 }}
                            ElseIf ((Local0 == 0x02))
                            {{
                                KBBR = DerefOf (WMIB [0x04])
                                KBTO = DerefOf (WMIB [0x05])
                            }}
                            Else {{ Local3 = 0x03 }}
                        }}
                        ElseIf ((Arg0 == 0x02))
                        {{
                            If (((Local0 == 0x07) && (Local1 == 0x02))) {{ OUTB [One] = CBST }}
                            ElseIf ((Local0 == One))
                            {{
                                OUTB [0x04] = KBBR
                                OUTB [0x05] = KBTO
                            }}
                            Else {{ Local3 = 0x03 }}
                        }}
                    }}

                    OUTB [Zero] = Local3
                    WMIB = OUTB
                }}

                {method("WMAA")}

                {method("WMBH")}
            }}
        }}
    }}
}}
"""

# The real tables use absolute paths for the EC; keep them, drop decompiler comments
asl = re.sub(r"/\*.*?\*/", "", asl)
open(sys.argv[2], "w").write(asl)
print(f"wrote {sys.argv[2]}")
