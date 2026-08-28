#!/usr/bin/env python3
"""
gen_cocobod_instrs.py  --  CPEN 438 / Project 3 "Hazard Watch"

Generates a team-unique, SEEDED COCOBOD regional cocoa-yield estimation
routine for the GhanaCore-5 five-stage pipeline, engineered to contain a
CONTROLLED hazard mix:

    * several immediately-forwardable RAW hazards (distance 1 -> EX/MEM path)
    * several distance-2 RAW hazards            (distance 2 -> MEM/WB path)
    * EXACTLY ONE load-use hazard               (unavoidable 1-cycle stall)
    * exactly one conditional branch            (resolved in ID, 1-cycle flush)

The seed varies register allocation, immediate values and the ordering of
independent instructions, so no two teams receive the same program while the
hazard *structure* (and therefore the marking scheme) stays identical.

Outputs, into --outdir:
    cocobod_seed<SEED>.asm   human-readable assembly (input to the C simulator)
    cocobod_seed<SEED>.hex   Logisim Evolution "v2.0 raw" ROM image
    cocobod_seed<SEED>.json  metadata + expected hazard inventory

Usage:
    python3 gen_cocobod_instrs.py --seed 4381 --outdir ../datasets
"""

import argparse
import json
import random
import os

# ----------------------------------------------------------------------------
# GhanaCore-5 instruction encoding (MIPS-like, 32-bit fixed width)
# ----------------------------------------------------------------------------
# R-type : op(6) rs(5) rt(5) rd(5) shamt(5) funct(6)
# I-type : op(6) rs(5) rt(5) imm(16)
#
# We keep the encoding deliberately MIPS-shaped so that the H&P Chapter 3
# figures (and any MIPS reference material) map onto our datapath 1:1.

R_TYPE_FUNCT = {
    "ADD": 0x20,
    "SUB": 0x22,
    "AND": 0x24,
    "OR":  0x25,
    "SLT": 0x2A,
}

I_TYPE_OP = {
    "ADDI": 0x08,
    "LW":   0x23,
    "SW":   0x2B,
    "BEQ":  0x04,
    "BNE":  0x05,
}


def encode(instr, labels, pc_index):
    """Second pass of the two-pass assembler: one instruction -> one 32-bit word."""
    mnem = instr["op"]

    if mnem == "NOP":
        return 0x00000000

    if mnem in R_TYPE_FUNCT:
        rd, rs, rt = instr["rd"], instr["rs"], instr["rt"]
        return (0 << 26) | (rs << 21) | (rt << 16) | (rd << 11) | (0 << 6) | R_TYPE_FUNCT[mnem]

    op = I_TYPE_OP[mnem]

    if mnem in ("BEQ", "BNE"):
        target = labels[instr["label"]]
        offset = target - (pc_index + 1)          # PC-relative, in instructions
        return (op << 26) | (instr["rs"] << 21) | (instr["rt"] << 16) | (offset & 0xFFFF)

    # ADDI / LW / SW
    return (op << 26) | (instr["rs"] << 21) | (instr["rt"] << 16) | (instr["imm"] & 0xFFFF)


def fmt(instr):
    """Pretty-print one instruction back to assembly text."""
    m = instr["op"]
    if m == "NOP":
        return "NOP"
    if m in R_TYPE_FUNCT:
        return f"{m:<4} R{instr['rd']}, R{instr['rs']}, R{instr['rt']}"
    if m in ("BEQ", "BNE"):
        return f"{m:<4} R{instr['rs']}, R{instr['rt']}, {instr['label']}"
    if m in ("LW", "SW"):
        return f"{m:<4} R{instr['rt']}, {instr['imm']}(R{instr['rs']})"
    return f"{m:<4} R{instr['rt']}, R{instr['rs']}, {instr['imm']}"


# ----------------------------------------------------------------------------
# Seeded program construction
# ----------------------------------------------------------------------------

def build_program(seed):
    rng = random.Random(seed)

    # --- seeded register allocation -----------------------------------------
    # A fixed pool of general-purpose registers is shuffled so each team's
    # dependence chain lives in different physical registers.
    pool = list(range(2, 24))
    rng.shuffle(pool)
    (BASE, RAIN, FERT, AGE, T1, T2, T3, T4,
     YIELD, THRESH, FLAG, T5, T6) = pool[:13]

    # --- seeded data parameters ---------------------------------------------
    base_addr = 4 * rng.randrange(4, 16)            # word-aligned record base
    rain_w    = rng.randrange(2, 9)                 # rainfall weight
    fert_w    = rng.randrange(2, 7)                 # fertiliser weight
    age_pen   = rng.randrange(1, 5)                 # tree-age penalty
    ceiling   = rng.randrange(600, 1400)            # yield ceiling (kg/ha)

    P = []   # program: list of instruction dicts
    C = []   # parallel list of source comments

    def emit(instr, comment):
        P.append(instr)
        C.append(comment)

    def R(op, rd, rs, rt):  return {"op": op, "rd": rd, "rs": rs, "rt": rt}
    def I(op, rt, rs, imm): return {"op": op, "rt": rt, "rs": rs, "imm": imm}
    def B(op, rs, rt, lab): return {"op": op, "rs": rs, "rt": rt, "label": lab}

    # ---- prologue -----------------------------------------------------------
    emit(I("ADDI", BASE, 0, base_addr), "R%d = base address of this region's record" % BASE)

    # ---- load the three predictor variables --------------------------------
    emit(I("LW", RAIN, BASE, 0), "rainfall_mm      <- MEM[base+0]")
    emit(I("LW", FERT, BASE, 4), "fertiliser_kg    <- MEM[base+4]")

    # *** THE load-use hazard: FERT is consumed by the very next instruction ***
    emit(R("ADD", T1, RAIN, FERT), "LOAD-USE: consumes FERT one cycle after its LW")

    emit(I("LW", AGE, BASE, 8), "tree_age_years   <- MEM[base+8]")

    # ---- weighted-sum yield model ------------------------------------------
    # Two independent instructions whose order the seed may swap: both write
    # distinct registers and read only already-defined ones, so swapping is
    # semantics-preserving but changes the hazard *distances* the team sees.
    a = I("ADDI", T2, T1, rain_w * fert_w)
    b = I("ADDI", THRESH, 0, ceiling)
    if rng.random() < 0.5:
        emit(a, "T2 = T1 + seeded weight   (RAW dist-1 on T1 -> EX/MEM forward)")
        emit(b, "THRESH = seeded yield ceiling")
    else:
        emit(b, "THRESH = seeded yield ceiling")
        emit(a, "T2 = T1 + seeded weight   (RAW dist-2 on T1 -> MEM/WB forward)")

    emit(R("ADD", T3, T2, AGE),   "T3 = T2 + tree_age        (RAW dist-1 and dist-N)")
    emit(I("ADDI", T4, T3, -age_pen), "T4 = T3 - age penalty     (RAW dist-1 chain)")
    emit(R("SUB", T5, T4, T2),    "T5 = T4 - T2              (RAW dist-1 + dist-2)")
    emit(R("AND", T6, T5, T4),    "T6 = T5 & T4              (RAW dist-1 + dist-2)")
    emit(I("ADDI", YIELD, T6, 0), "YIELD = T6                (RAW dist-1)")

    # ---- bounds check: the single conditional branch ------------------------
    emit(R("SLT", FLAG, YIELD, THRESH), "FLAG = (YIELD < ceiling)? (RAW dist-1 on YIELD)")
    emit(B("BEQ", FLAG, 0, "STORE"),    "BRANCH: if FLAG==0 skip the clamp  (resolved in ID)")
    emit(I("ADDI", YIELD, THRESH, 0),   "clamp YIELD to the ceiling (branch-not-taken path)")

    store_label_index = len(P)

    # ---- epilogue: write the estimate back ----------------------------------
    emit(I("SW", YIELD, BASE, 12), "STORE: MEM[base+12] <- estimated yield")
    emit(I("ADDI", T1, YIELD, rng.randrange(1, 20)), "running regional accumulator")
    emit(R("ADD", T2, T1, YIELD), "T2 = T1 + YIELD           (RAW dist-1 + dist-2)")
    emit(I("SW", T2, BASE, 16),   "MEM[base+16] <- accumulator (SW reads rt as a SOURCE)")

    labels = {"STORE": store_label_index}

    meta = {
        "seed": seed,
        "registers": {
            "BASE": BASE, "RAIN": RAIN, "FERT": FERT, "AGE": AGE,
            "T1": T1, "T2": T2, "T3": T3, "T4": T4, "T5": T5, "T6": T6,
            "YIELD": YIELD, "THRESH": THRESH, "FLAG": FLAG,
        },
        "data": {
            "base_addr": base_addr, "rain_weight": rain_w,
            "fert_weight": fert_w, "age_penalty": age_pen,
            "yield_ceiling": ceiling,
        },
        "instruction_count": len(P),
        "expected_hazard_inventory": {
            "load_use_hazards": 1,
            "conditional_branches": 1,
            "note": "RAW counts are produced by the simulator's classifier, "
                    "not asserted here -- the team must derive them by hand "
                    "first and then check against the simulator.",
        },
    }
    return P, C, labels, meta


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True,
                    help="the team's assigned integrity seed (Part I s7)")
    ap.add_argument("--outdir", default="../datasets")
    ap.add_argument("--team", default="TEAM-UNSET")
    args = ap.parse_args()

    P, C, labels, meta = build_program(args.seed)
    meta["team"] = args.team
    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.join(args.outdir, f"cocobod_seed{args.seed}")

    # ---- .asm ---------------------------------------------------------------
    inv_labels = {v: k for k, v in labels.items()}
    lines = [
        f"; COCOBOD regional cocoa-yield estimation routine",
        f"; CPEN 438 Project 3 -- Hazard Watch",
        f"; team={args.team}  seed={args.seed}  instructions={len(P)}",
        f"; yield = f(rainfall, fertiliser, tree_age), clamped to a ceiling",
        f";",
        f"; register map: " + ", ".join(f"{k}=R{v}" for k, v in meta["registers"].items()),
        "",
    ]
    for i, (ins, cmt) in enumerate(zip(P, C)):
        if i in inv_labels:
            lines.append(f"{inv_labels[i]}:")
        lines.append(f"    {fmt(ins):<28}; [{i:02d}] {cmt}")
    with open(stem + ".asm", "w") as f:
        f.write("\n".join(lines) + "\n")

    # ---- .hex (Logisim Evolution ROM/RAM image) -----------------------------
    words = [encode(ins, labels, i) for i, ins in enumerate(P)]
    with open(stem + ".hex", "w") as f:
        f.write("v2.0 raw\n")
        for j in range(0, len(words), 8):
            f.write(" ".join(f"{w:08x}" for w in words[j:j + 8]) + "\n")

    # ---- .json --------------------------------------------------------------
    meta["machine_code"] = [f"0x{w:08x}" for w in words]
    with open(stem + ".json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"wrote {stem}.asm  ({len(P)} instructions)")
    print(f"wrote {stem}.hex  (Logisim v2.0 raw image)")
    print(f"wrote {stem}.json")


if __name__ == "__main__":
    main()
