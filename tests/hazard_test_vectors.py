#!/usr/bin/env python3
"""
hazard_test_vectors.py  --  CPEN 438 / Project 3 "Hazard Watch"

Project 3 section G requires "at least three hand-derived hazard test vectors
(immediate RAW, load-use, branch) verified against hand-traced expected
behaviour BEFORE running the full program."

This file is that requirement, discharged. Every EXPECTED number below was
derived BY HAND from the pipeline timing rules and is written here as an
assertion, so the simulator is checked against the hand trace rather than the
hand trace being written to match whatever the simulator happened to print.

Timing rule used for every hand derivation:

    cycles = (useful instructions retired) + 4 + (stall cycles) + (flush cycles)

    ... because the first instruction retires in cycle 5 and one further
    instruction retires every cycle thereafter, unless a bubble intervenes.

Run:  python3 hazard_test_vectors.py --sim ../src/hazard_sim
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# Data memory in the simulator is initialised to MEM[word i] = (37*i + 11) % 1000
# so hand-derived load values are deterministic.
# ---------------------------------------------------------------------------
def dmem(word):
    return (37 * word + 11) % 1000


VECTORS = [
    # ---------------------------------------------------------------- V1 ---
    dict(
        name="V1  immediate RAW, distance 1 -> EX/MEM forward, zero stalls",
        why="Back-to-back dependent ALU ops. Forwarding must resolve every one "
            "of them with no stall at all. If this vector stalls, the "
            "forwarding unit is not firing.",
        asm="""
            ADDI R1, R0, 5
            ADDI R2, R1, 3
            ADD  R3, R2, R1
            SUB  R4, R3, R2
        """,
        expect=dict(cycles=8, useful=4, stall_loaduse=0, stall_branch=0, flushes=0),
        expect_regs={1: 5, 2: 8, 3: 13, 4: 5},
    ),

    # ---------------------------------------------------------------- V2 ---
    dict(
        name="V2  RAW, distance 2 -> MEM/WB forward, zero stalls",
        why="The producer is two slots back. With a write-first-half / "
            "read-second-half register file this is still NOT visible in ID, "
            "so the MEM/WB path must supply it. A pipeline that only "
            "implements EX/MEM forwarding fails here.",
        asm="""
            ADDI R1, R0, 5
            ADDI R9, R0, 1
            ADDI R2, R1, 3
        """,
        expect=dict(cycles=7, useful=3, stall_loaduse=0, stall_branch=0, flushes=0),
        expect_regs={1: 5, 9: 1, 2: 8},
    ),

    # ---------------------------------------------------------------- V3 ---
    dict(
        name="V3  LOAD-USE -> exactly ONE stall, forwarding cannot help",
        why="The one hazard forwarding cannot fix. The load's data leaves "
            "memory at the END of MEM; the consumer wants it at the START of "
            "its EX. Exactly one bubble, never zero and never two.",
        asm="""
            ADDI R1, R0, 0
            LW   R2, 0(R1)
            ADD  R3, R2, R2
        """,
        expect=dict(cycles=8, useful=3, stall_loaduse=1, stall_branch=0, flushes=0),
        expect_regs={1: 0, 2: dmem(0), 3: 2 * dmem(0)},
    ),

    # ---------------------------------------------------------------- V4 ---
    dict(
        name="V4  BRANCH TAKEN -> 1 branch-data stall + 1 flush",
        why="Branch resolved in ID. Its second operand is produced by the "
            "instruction immediately before it (still in EX), so one stall. "
            "Once taken, the instruction already fetched behind it is wrong "
            "and must be flushed: one further cycle. The skipped instruction "
            "must never write its register.",
        asm="""
            ADDI R1, R0, 7
            ADDI R2, R0, 7
            BEQ  R1, R2, SKIP
            ADDI R3, R0, 999
        SKIP:
            ADDI R4, R0, 42
        """,
        expect=dict(cycles=10, useful=4, stall_loaduse=0, stall_branch=1, flushes=1),
        expect_regs={1: 7, 2: 7, 3: 0, 4: 42},   # R3 MUST stay 0
    ),

    # ---------------------------------------------------------------- V5 ---
    dict(
        name="V5  BRANCH NOT TAKEN -> 1 branch-data stall, NO flush",
        why="Same shape as V4 but the comparison fails. The fall-through "
            "instruction must execute and there must be no flush cycle. A "
            "pipeline that flushes unconditionally passes V4 and fails here.",
        asm="""
            ADDI R1, R0, 7
            ADDI R2, R0, 9
            BEQ  R1, R2, SKIP
            ADDI R3, R0, 999
        SKIP:
            ADDI R4, R0, 42
        """,
        expect=dict(cycles=10, useful=5, stall_loaduse=0, stall_branch=1, flushes=0),
        expect_regs={1: 7, 2: 9, 3: 999, 4: 42},
    ),

    # ---------------------------------------------------------------- V6 ---
    dict(
        name="V6  FORWARDING PRIORITY -- the most recent producer must win",
        why="Two instructions write the SAME register back to back. At the "
            "consumer's EX, both EX/MEM and MEM/WB match. EX/MEM is the newer "
            "value and must take priority. Getting this backwards passes V1 "
            "and V2 and silently returns the STALE value here. This is the "
            "bug the instructor notes say will be probed at the defence.",
        asm="""
            ADDI R1, R0, 111
            ADDI R1, R0, 222
            ADD  R2, R1, R0
        """,
        expect=dict(cycles=7, useful=3, stall_loaduse=0, stall_branch=0, flushes=0),
        expect_regs={1: 222, 2: 222},            # 111 here means priority is inverted
    ),

    # ---------------------------------------------------------------- V7 ---
    dict(
        name="V7  SW reads rt as a SOURCE -- hazard on store data",
        why="A hazard-detection unit that only checks rs misses this. The "
            "store's data operand comes from the immediately preceding load, "
            "so it is a genuine load-use hazard and must stall.",
        asm="""
            ADDI R1, R0, 0
            LW   R2, 0(R1)
            SW   R2, 32(R1)
        """,
        expect=dict(cycles=8, useful=3, stall_loaduse=1, stall_branch=0, flushes=0),
        expect_regs={1: 0, 2: dmem(0)},
    ),
]


# ---------------------------------------------------------------------------

def parse_output(out):
    got = {}
    m = re.search(r"cycles=(\d+)\s+useful=(\d+)\s+CPI=([\d.]+)\s+"
                  r"stalls\(lu/br\)=(\d+)/(\d+)\s+flushes=(\d+)", out)
    if not m:
        return None, None
    got["cycles"]        = int(m.group(1))
    got["useful"]        = int(m.group(2))
    got["stall_loaduse"] = int(m.group(4))
    got["stall_branch"]  = int(m.group(5))
    got["flushes"]       = int(m.group(6))

    regs = {}
    for r, v in re.findall(r"R(\d+)=(-?\d+)", out):
        regs[int(r)] = int(v)
    return got, regs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default="../src/hazard_sim")
    ap.add_argument("--keep", action="store_true", help="keep the generated .asm files")
    args = ap.parse_args()

    sim = os.path.abspath(args.sim)
    if not os.path.exists(sim):
        sys.exit(f"simulator not found at {sim} -- build it with "
                 f"gcc -O2 -o hazard_sim hazard_pipeline_sim.c")

    tmp = tempfile.mkdtemp(prefix="hazvec_")
    passed = failed = 0

    print("=" * 74)
    print(" PROJECT 3 -- HAND-DERIVED HAZARD TEST VECTORS")
    print(" every expected value below was derived by hand before running the sim")
    print("=" * 74)

    for v in VECTORS:
        asm = "\n".join(l.strip() for l in v["asm"].strip().splitlines()) + "\n"
        path = os.path.join(tmp, v["name"].split()[0] + ".asm")
        with open(path, "w") as f:
            f.write(asm)

        out = subprocess.run([sim, path, "--mode", "hazard"],
                             capture_output=True, text=True).stdout
        got, regs = parse_output(out)

        print(f"\n{v['name']}")
        print(f"  rationale: {v['why']}")

        problems = []
        if got is None:
            problems.append("could not parse simulator output")
        else:
            for k, want in v["expect"].items():
                if got[k] != want:
                    problems.append(f"{k}: expected {want}, got {got[k]}")
            for r, want in v["expect_regs"].items():
                have = regs.get(r, 0)
                if have != want:
                    problems.append(f"R{r}: expected {want}, got {have}")

        if problems:
            failed += 1
            print("  RESULT: FAIL")
            for p in problems:
                print(f"          - {p}")
        else:
            passed += 1
            e = v["expect"]
            print(f"  RESULT: PASS   cycles={e['cycles']}  "
                  f"load-use stalls={e['stall_loaduse']}  "
                  f"branch stalls={e['stall_branch']}  flushes={e['flushes']}")

    # ---- negative test: prove the HDU is load-bearing -----------------------
    print("\n" + "=" * 74)
    print(" NEGATIVE TEST -- disable the hazard-detection unit and confirm the")
    print(" load-use case is caught rather than silently mis-forwarded")
    print("=" * 74)
    v3 = os.path.join(tmp, "V3.asm")
    r = subprocess.run([sim, v3, "--mode", "hazard", "--no-hdu"],
                       capture_output=True, text=True)
    if r.returncode == 2 and "DETECTED" in r.stderr:
        passed += 1
        print("  RESULT: PASS -- the guard fired:")
        for line in r.stderr.strip().splitlines():
            print("    " + line)
    else:
        failed += 1
        print(f"  RESULT: FAIL -- expected the guard to fire "
              f"(returncode={r.returncode})")

    print("\n" + "=" * 74)
    print(f" {passed} passed, {failed} failed")
    print("=" * 74)
    if args.keep:
        print(f" vectors kept in {tmp}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
