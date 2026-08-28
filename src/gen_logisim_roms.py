#!/usr/bin/env python3
"""
gen_logisim_roms.py -- produce memory images that Logisim Evolution can load
directly into a ROM/RAM component (right-click the component -> "Load Image...").

Writes, for a given .hex program and .data file:
    <name>_imem.rom     instruction ROM   (v2.0 raw, 32-bit words)
    <name>_dmem.rom     data RAM image    (v2.0 raw, 32-bit words)
    <name>_imem.txt     human-readable listing (address, word, disassembly)

Usage:
    python3 tools/gen_logisim_roms.py --hex results/cocobod_seed16993.hex \
        --data results/cocobod_seed16993.data --outdir logisim
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p3isa as isa


def read_hex(path):
    """Accept either one-word-per-line hex (with optional '#' comments) or a
    Logisim 'v2.0 raw' image, which packs several words on a line."""
    words, comments = [], []
    for line in open(path):
        code = line.split("#")[0].split(";")[0].strip()
        cmt = line.split("#")[1].strip() if "#" in line else ""
        if not code or code.lower().startswith(("v2.0", "v3.0")):
            continue
        toks = code.split()
        for t in toks:
            words.append(int(t, 16))
            comments.append(cmt if len(toks) == 1 else "")
    return words, comments


def write_v2_raw(path, words, width_words=None):
    """Logisim 'v2.0 raw' image: 16 values per line, hex, no 0x prefix."""
    if width_words:
        words = list(words) + [0] * max(0, width_words - len(words))
    with open(path, "w") as f:
        f.write("v2.0 raw\n")
        for i in range(0, len(words), 16):
            f.write(" ".join("%x" % w for w in words[i:i + 16]) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hex", required=True)
    ap.add_argument("--data", default=None)
    ap.add_argument("--asm", default=None,
                    help="optional .asm source, used to annotate the listing")
    ap.add_argument("--outdir", default="logisim")
    ap.add_argument("--imem-words", type=int, default=64,
                    help="pad the instruction ROM to this many words")
    ap.add_argument("--dmem-words", type=int, default=128,
                    help="pad the data RAM to this many words")
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    name = os.path.splitext(os.path.basename(a.hex))[0]
    words, comments = read_hex(a.hex)
    if a.asm and os.path.exists(a.asm):
        import p3isa as _isa
        prog = _isa.load_asm_file(a.asm)
        if len(prog) == len(words):
            comments = [" ".join(_isa.to_asm([i]).split()) for i in prog]

    irom = os.path.join(a.outdir, name + "_imem.rom")
    write_v2_raw(irom, words, a.imem_words)

    listing = os.path.join(a.outdir, name + "_imem.txt")
    with open(listing, "w") as f:
        f.write("addr(byte)  word      instruction\n")
        f.write("-" * 52 + "\n")
        for i, w in enumerate(words):
            f.write("0x%04x      %08x  %s\n" % (i * 4, w, comments[i]))

    # Week 1 data-memory convention: MEM[word i] = (37*i + 11) % 1000
    dwords = [(37 * i + 11) % 1000 for i in range(a.dmem_words)]
    if a.data and os.path.exists(a.data):
        for line in open(a.data):
            line = line.split("#")[0].split()
            if len(line) == 2:
                addr, val = int(line[0]), int(line[1])
                if 0 <= addr // 4 < a.dmem_words:
                    dwords[addr // 4] = val & 0xFFFFFFFF
    drom = os.path.join(a.outdir, name + "_dmem.rom")
    write_v2_raw(drom, dwords)

    print("instruction ROM image .. %s   (%d words, padded to %d)"
          % (irom, len(words), a.imem_words))
    print("data RAM image ......... %s   (%d words)" % (drom, a.dmem_words))
    print("listing ................ %s" % listing)
    print("\nIn Logisim Evolution: right-click the ROM -> Load Image... -> pick the")
    print(".rom file. The ROM must be configured 32-bit data, and the address bit")
    print("width must be at least %d." % max(6, (a.imem_words - 1).bit_length()))
    print("Remember the ROM is word-addressed: drive it with PC[7:2], not PC[7:0].")


if __name__ == "__main__":
    main()
