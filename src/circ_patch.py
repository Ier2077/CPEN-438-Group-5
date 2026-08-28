#!/usr/bin/env python3
"""
circ_patch.py -- apply a small, verified edit to a Logisim .circ file.

Currently one patch is implemented:

    --gate-isbranch
        AND `IsBranch` into `ForwardC` and `ForwardD` in the
        `Branch_Forward_Stall` subcircuit.

        Why: the Week 1 equation for ForwardC/ForwardD has no IsBranch term,
        so the unit asserts them whenever EX/MEM happens to match IF/ID.Rs or
        IF/ID.Rt, whether or not the instruction in ID is a branch. That is
        harmless as long as PCSrc is gated by IsBranch elsewhere, but it makes
        the signals mean something other than their names, and it makes the
        cycle-by-cycle comparison against the golden simulator diverge on
        cycles where nothing is actually wrong. One AND gate each fixes it.

The edit is verified rather than assumed. After patching, the tool re-extracts
the netlist from both files and checks that:
  * every gate port still lands on a wire
  * the electrical partition of the ORIGINAL nets is unchanged -- no two
    previously separate nets have been accidentally shorted by the new wiring
  * the new gate ports do not collide with anything that was already there

If any check fails the patched file is not written.

Usage:
    python3 tools/circ_patch.py --in  logisim/GhanaCore5_HazardUnits.circ \
                                --out logisim/GhanaCore5_HazardUnits_gated.circ \
                                --gate-isbranch
"""
import argparse
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logisim_check import Circuit, parse_loc

GATE_SIZE = 50          # matches the gates already in the file


def loc_str(p):
    return "(%d,%d)" % p


def endpoints_and_locs(circ_elem):
    eps, locs = set(), set()
    for w in circ_elem.findall("wire"):
        eps.add(parse_loc(w.get("from")))
        eps.add(parse_loc(w.get("to")))
    for c in circ_elem.findall("comp"):
        if c.get("loc"):
            locs.add(parse_loc(c.get("loc")))
    return eps, locs


def find_pin(circ_elem, label):
    for c in circ_elem.findall("comp"):
        if c.get("name") != "Pin":
            continue
        attrs = {a.get("name"): a.get("val") for a in c.findall("a")}
        if attrs.get("label") == label:
            return c, parse_loc(c.get("loc"))
    return None, None


def add_and_gate(circ_elem, out_loc, in_locs):
    comp = ET.SubElement(circ_elem, "comp")
    comp.set("lib", "1")
    comp.set("loc", loc_str(out_loc))
    comp.set("name", "AND Gate")
    for k, v in (("inputs", "2"), ("size", str(GATE_SIZE))):
        a = ET.SubElement(comp, "a")
        a.set("name", k)
        a.set("val", v)
    return comp


def add_wire(circ_elem, a, b):
    w = ET.SubElement(circ_elem, "wire")
    w.set("from", loc_str(a))
    w.set("to", loc_str(b))
    return w


def gate_isbranch(root, log):
    circ = None
    for c in root.findall("circuit"):
        if c.get("name") == "Branch_Forward_Stall":
            circ = c
    if circ is None:
        log.append("no Branch_Forward_Stall circuit found")
        return False

    _, isbranch_loc = find_pin(circ, "IsBranch")
    if isbranch_loc is None:
        log.append("no IsBranch pin found")
        return False

    eps, locs = endpoints_and_locs(circ)
    ok = True
    for label in ("ForwardC", "ForwardD"):
        _, pin_loc = find_pin(circ, label)
        if pin_loc is None:
            log.append("no %s pin found" % label)
            ok = False
            continue

        driver = None
        for w in list(circ.findall("wire")):
            a, b = parse_loc(w.get("from")), parse_loc(w.get("to"))
            if pin_loc in (a, b):
                driver = b if a == pin_loc else a
                circ.remove(w)
                break
        if driver is None:
            log.append("%s: could not find the wire driving the pin" % label)
            ok = False
            continue

        in_hi = (pin_loc[0] - GATE_SIZE, pin_loc[1] - 10)
        in_lo = (pin_loc[0] - GATE_SIZE, pin_loc[1] + 10)
        for p in (in_hi, in_lo):
            if p in eps or p in locs:
                log.append("%s: new gate port %s collides with existing wiring"
                           % (label, p))
                ok = False
        if not ok:
            continue

        add_and_gate(circ, pin_loc, (in_hi, in_lo))
        add_wire(circ, driver, in_hi)        # the original ForwardC/D term
        add_wire(circ, isbranch_loc, in_lo)  # ... now ANDed with IsBranch
        eps |= {in_hi, in_lo, driver, isbranch_loc}
        log.append("%s: inserted AND gate at %s, inputs %s (was %s) and %s "
                   "(IsBranch)" % (label, pin_loc, in_hi, driver, in_lo))
    return ok


def net_partition(circ_elem):
    """Map each wire endpoint to a canonical representative of its net."""
    c = Circuit(circ_elem)
    return {p: c.node(p) for p in
            {parse_loc(w.get(k)) for w in circ_elem.findall("wire")
             for k in ("from", "to")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--gate-isbranch", action="store_true")
    a = ap.parse_args()
    if not a.gate_isbranch:
        ap.error("nothing to do; pass --gate-isbranch")

    before_tree = ET.parse(a.src)
    after_tree = ET.parse(a.src)
    log = []

    if not gate_isbranch(after_tree.getroot(), log):
        print("\n".join(log))
        sys.exit("patch aborted; nothing written")

    # ---- verify -----------------------------------------------------------
    problems = []
    for bc, ac in zip(before_tree.getroot().findall("circuit"),
                      after_tree.getroot().findall("circuit")):
        after = Circuit(ac)
        if after.problems:
            problems += ["%s: %s" % (ac.get("name"), p)
                         for p in sorted(set(after.problems))]
        # every pair of points that were on DIFFERENT nets before must still be
        # on different nets afterwards
        pb, pa = net_partition(bc), net_partition(ac)
        pts = sorted(pb)
        groups = {}
        for p in pts:
            groups.setdefault(pb[p], []).append(p)
        reps = {k: v[0] for k, v in groups.items()}
        keys = list(reps)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                p, q = reps[keys[i]], reps[keys[j]]
                if pa.get(p) == pa.get(q):
                    problems.append("%s: nets at %s and %s were separate and are "
                                    "now shorted" % (ac.get("name"), p, q))

    print("\n".join(log))
    if problems:
        print("\nVERIFICATION FAILED:")
        for p in problems[:10]:
            print("  - " + p)
        sys.exit("patch aborted; nothing written")

    after_tree.write(a.dst, encoding="UTF-8", xml_declaration=True)
    print("\nverification: every gate port lands on a wire, and no two "
          "previously separate nets were shorted")
    print("written: %s" % a.dst)


if __name__ == "__main__":
    main()
