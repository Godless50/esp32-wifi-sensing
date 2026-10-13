#!/usr/bin/env python3
"""Расшифровать падения плат из /api/devices (поле diag): адрес -> функция, файл:строка.

usage: python3 tools/decode_diag.py [MAC ...]
Нужны data/ml/fw_<id>.elf.xz (их кладёт tools/push_fw.sh) и образ espressif/idf (addr2line, ELF ПЗУ).
Адреса из ПЗУ (0x40000000..0x4005ffff) ищутся в ELF ПЗУ ESP32-S3, остальные — в ELF прошивки.
"""
import json
import lzma
import os
import subprocess
import sys
import tempfile
import urllib.request

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = "espressif/idf:release-v5.4"


def addr2line(elf_dir: str, elf: str, addrs: list[str]) -> list[str]:
    if not addrs:
        return []
    cmd = ("R=$(ls -d /opt/esp/tools/esp-rom-elfs/*/ | head -1); "
           "for a in " + " ".join(addrs) + "; do "
           "if [ $((a)) -lt $((0x40060000)) ]; then E=${R}esp32s3_rev0_rom.elf; else E=/e/" + elf + "; fi; "
           "xtensa-esp32s3-elf-addr2line -pfiaC -e $E $a; done")
    out = subprocess.run(["docker", "run", "--rm", "-v", f"{elf_dir}:/e:ro", "--entrypoint", "bash", IMG, "-c",
                          ". /opt/esp/idf/export.sh >/dev/null 2>&1; " + cmd],
                         capture_output=True, text=True, timeout=120).stdout
    return [l for l in out.splitlines() if l.strip()]


def main(macs: list[str]) -> None:
    with urllib.request.urlopen("http://127.0.0.1:8080/api/devices", timeout=5) as r:
        devs = json.load(r)["devices"]
    # не /tmp: docker из snap не видит /tmp хоста
    with tempfile.TemporaryDirectory(dir=os.path.join(R, "data")) as tmp:
        for d in devs:
            if macs and d["device_id"] not in macs:
                continue
            g = d.get("diag")
            fw = (d.get("telemetry") or {}).get("fw_ver") or 0
            print(f"== {d['device_id']} fw {fw:08x}")
            if not g:
                print("   no diag yet")
                continue
            print(f"   previous boot ran {g.get('prev_uptime_s')} s; resets: {' '.join(g['last'][:6])}; "
                  f"rom: {g.get('rom_reset')}")
            p, e = g.get("panic"), g.get("early")
            if p:
                print(f"   panic {p['kind']} core {p['core']}: {p['msg']}")
            if e:
                print(f"   entered {e['kind']} vector: {e['cause']} at {e['pc']}")
            addrs = ([p["pc"]] if p else []) + ([e["pc"]] if e and not p else []) + g.get("backtrace", [])
            src = os.path.join(R, "data", "ml", f"fw_{fw:08x}.elf.xz")
            elf = f"fw_{fw:08x}.elf"
            if os.path.exists(src):
                with lzma.open(src) as fi, open(os.path.join(tmp, elf), "wb") as fo:
                    fo.write(fi.read())
            elif any(int(a, 16) >= 0x40060000 for a in addrs):
                print(f"   (no {os.path.basename(src)}: firmware addresses can not be decoded)")
            for line in addr2line(tmp, elf, addrs):
                print("   " + line)


if __name__ == "__main__":
    main(sys.argv[1:])
