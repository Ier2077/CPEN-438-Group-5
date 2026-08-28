#!/usr/bin/env python3
"""
logisim_vs_sim.py -- cycle-by-cycle cross-validation of the Logisim hazard
subcircuits against the golden simulator, on the real COCOBOD routine.

This is the Section G requirement done end to end and without hand-copying
numbers off a screen:

  1. the golden simulator runs the routine and writes, for every clock cycle,
     the exact stimulus the three hazard units would see in hardware
     (`hazard_sim prog.asm --unit-csv FILE`)
  2. the netlist of each Logisim subcircuit is extracted from the .circ file
     and evaluated on that stimulus
  3. the circuit's outputs are compared with the decisions the simulator
     actually took that cycle

A pass means the circuit and the simulator agree on every forwarding decision,
every stall and every branch-comparator forward, cycle for cycle, on the
assigned program -- not merely on a handful of poked test cases.

Usage:
    python3 tools/logisim_vs_sim.py --circ logisim/GhanaCore5_HazardUnits.circ \
                                    --units results/unit_stimulus.csv \
                                    --report results/logisim_vs_sim.txt
"""
import argparse
import csv
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logisim_check import Circuit


def bits(v, n=5):
    return [(v >> i) & 1 for i in range(n)]


def build_inputs(circ_name, row):
    """Map one stimulus row onto the input pin names of one subcircuit."""
    g = lambda k: int(row[k])
    v = {}
    if circ_name == "Forwarding_Unit":
        for i, b in enumerate(bits(g("EXMEM_Rd"))):
            v["EXMEM_Rd[%d]" % i] = b
        for i, b in enumerate(bits(g("MEMWB_Rd"))):
            v["MEMWB_Rd[%d]" % i] = b
        for i, b in enumerate(bits(g("IDEX_Rs"))):
            v["IDEX_Rs[%d]" % i] = b
        for i, b in enumerate(bits(g("IDEX_Rt"))):
            v["IDEX_Rt[%d]" % i] = b
        v["EXMEM_RegWrite"] = g("EXMEM_RegWrite")
        v["MEMWB_RegWrite"] = g("MEMWB_RegWrite")
        v["EXMEM_Rd_NZ"] = int(g("EXMEM_Rd") != 0)
        v["MEMWB_Rd_NZ"] = int(g("MEMWB_Rd") != 0)
    elif circ_name == "Hazard_Detection_Unit":
        for i, b in enumerate(bits(g("IDEX_Rt"))):
            v["IDEX_Rt[%d]" % i] = b
        for i, b in enumerate(bits(g("IFID_Rs"))):
            v["IFID_Rs[%d]" % i] = b
        for i, b in enumerate(bits(g("IFID_Rt"))):
            v["IFID_Rt[%d]" % i] = b
        v["IDEX_MemRead"] = g("IDEX_MemRead")
        v["IDEX_Rt_NZ"] = int(g("IDEX_Rt") != 0)
        v["IFID_UsesRs"] = g("IFID_UsesRs")
        v["IFID_UsesRt"] = g("IFID_UsesRt")
    elif circ_name == "Branch_Forward_Stall":
        for i, b in enumerate(bits(g("IDEX_Rd"))):
            v["IDEX_Rd[%d]" % i] = b
        for i, b in enumerate(bits(g("EXMEM_Rd"))):
            v["EXMEM_Rd[%d]" % i] = b
        for i, b in enumerate(bits(g("IFID_Rs"))):
            v["IFID_Rs[%d]" % i] = b
        for i, b in enumerate(bits(g("IFID_Rt"))):
            v["IFID_Rt[%d]" % i] = b
        v["IsBranch"] = g("IsBranch")
        v["IDEX_RegWrite"] = g("IDEX_RegWrite")
        v["IDEX_Rd_NZ"] = int(g("IDEX_Rd") != 0)
        v["EXMEM_RegWrite"] = g("EXMEM_RegWrite")
        v["EXMEM_Rd_NZ"] = int(g("EXMEM_Rd") != 0)
        v["EXMEM_MemRead"] = g("EXMEM_MemRead")
    return v


def expected(circ_name, row):
    g = lambda k: int(row[k])
    if circ_name == "Forwarding_Unit":
        return {"ForwardA_bit1": g("exp_ForwardA_bit1"),
                "ForwardA_bit0": g("exp_ForwardA_bit0"),
                "ForwardB_bit1": g("exp_ForwardB_bit1"),
                "ForwardB_bit0": g("exp_ForwardB_bit0")}
    if circ_name == "Hazard_Detection_Unit":
        return {"Stall": g("exp_Stall")}
    if circ_name == "Branch_Forward_Stall":
        cd = g("exp_ForwardCD")
        return {"BranchStall": g("exp_BranchStall"),
                "ForwardC": cd & 1, "ForwardD": (cd >> 1) & 1}
    return {}


def fmt(v):
    """Compact rendering of a forwarding decision for the report."""
    a = "10" if v.get("ForwardA_bit1") else ("01" if v.get("ForwardA_bit0") else "00")
    b = "10" if v.get("ForwardB_bit1") else ("01" if v.get("ForwardB_bit0") else "00")
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--circ", required=True)
    ap.add_argument("--units", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()

    root = ET.parse(a.circ).getroot()
    circuits = {c.get("name"): Circuit(c) for c in root.findall("circuit")}
    rows = list(csv.DictReader(open(a.units)))

    L = ["LOGISIM SUBCIRCUITS vs GOLDEN SIMULATOR, CYCLE BY CYCLE",
         "=" * 78,
         "circuit file : %s" % a.circ,
         "stimulus     : %s (%d cycles of the assigned COCOBOD routine)"
         % (a.units, len(rows)),
         ""]

    bad = 0
    per_cycle = ["cycle  ForwardA ForwardB  Stall BranchStall FwdC FwdD   verdict",
                 "-" * 66]
    for row in rows:
        got_all, want_all = {}, {}
        for name, circ in circuits.items():
            if name not in ("Forwarding_Unit", "Hazard_Detection_Unit",
                            "Branch_Forward_Stall"):
                continue
            v = build_inputs(name, row)
            missing = [lab for lab, _ in circ.inputs if lab not in v]
            if missing:
                L.append("cycle %s: circuit %s has unmapped input pins: %s"
                         % (row["cycle"], name, ", ".join(missing)))
                bad += 1
                continue
            got_all.update(circ.evaluate(v))
            want_all.update(expected(name, row))

        diffs = [k for k in want_all if got_all.get(k) != want_all[k]]
        fa, fb = fmt(got_all)
        per_cycle.append("%5s     %-8s %-8s %-5s %-11s %-4s %-4s %s"
                         % (row["cycle"], fa, fb, got_all.get("Stall"),
                            got_all.get("BranchStall"), got_all.get("ForwardC"),
                            got_all.get("ForwardD"),
                            "ok" if not diffs else "MISMATCH " + ",".join(diffs)))
        if diffs:
            bad += 1
            if bad <= a.limit:
                for k in diffs:
                    L.append("cycle %s: %s -- circuit says %s, simulator says %s"
                             % (row["cycle"], k, got_all.get(k), want_all[k]))

    L += per_cycle
    L += ["", "=" * 78,
          "%d cycles compared" % len(rows),
          "RESULT: %s" % ("PASS -- the circuit reproduces every forwarding, stall "
                          "and branch decision the golden simulator took"
                          if bad == 0 else "FAIL -- %d mismatching cycle(s)" % bad)]
    text = "\n".join(L) + "\n"
    print(text)
    if a.report:
        open(a.report, "w").write(text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
