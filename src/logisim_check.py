#!/usr/bin/env python3
"""
logisim_check.py -- read a Logisim Evolution .circ file, extract the gate-level
netlist geometrically, simulate it, and check every output against a reference
model of the Week 1 truth tables.

Why this exists: Logisim Evolution's own `--test-vector` runner needs a windowing
system, which makes it awkward to run in a marking script or CI. The circuit
itself is pure combinational logic (AND / OR / NOT / XNOR gates, pins and wires),
so it can be read straight out of the XML and evaluated.

How the netlist is recovered:
  * every <wire> is a segment between two exact coordinates; coordinates joined
    by wires form one electrical node (union-find)
  * a gate's OUTPUT sits at its `loc`; its INPUT coordinates follow Logisim's
    own offset rule for (facing, size, number of inputs)
  * the extraction is self-checking: every computed input coordinate must
    coincide with a wire endpoint or another component's port, otherwise the
    geometry is reported as unresolved rather than silently guessed

Usage:
    python3 tools/logisim_check.py --circ logisim/GhanaCore5_HazardUnits.circ
    python3 tools/logisim_check.py --circ FILE --dump Forwarding_Unit
    python3 tools/logisim_check.py --circ FILE --vectors 5000 --report FILE
"""
import argparse
import itertools
import random
import sys
import xml.etree.ElementTree as ET

GATES = {"AND Gate", "OR Gate", "NOT Gate", "XNOR Gate", "XOR Gate",
         "NAND Gate", "NOR Gate", "Buffer"}


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def parse_loc(s):
    x, y = s.strip("()").split(",")
    return (int(x), int(y))


def input_offsets(size, inputs):
    """Logisim's AbstractGate input offsets along the gate's axis, for a gate
    facing east: input i sits at (-size, dy)."""
    if inputs <= 3:
        # Logisim spaces up to three inputs 10 units apart whatever the gate
        # width; the wider body just makes the wedge longer.
        skip_start, skip_dist, skip_lower_even = -5, 10, 10
    elif inputs == 4 and size >= 50:
        skip_start, skip_dist, skip_lower_even = -5, 20, 0
    else:
        skip_start, skip_dist, skip_lower_even = -5, 10, 10

    offs = []
    for i in range(inputs):
        if inputs % 2 == 1:
            dy = skip_start * (inputs - 1) + skip_dist * i
        else:
            dy = skip_start * inputs + skip_dist * i
            if i >= inputs // 2:
                dy += skip_lower_even
        offs.append(dy)
    return offs


def rotate(dx, dy, facing):
    if facing == "east":
        return (dx, dy)
    if facing == "west":
        return (-dx, -dy)
    if facing == "south":
        return (-dy, dx)
    if facing == "north":
        return (dy, -dx)
    raise ValueError(facing)


def gate_ports(loc, facing, size, inputs):
    """Return (output_coord, [input_coords]) for a gate."""
    out = loc
    ins = []
    for dy in input_offsets(size, inputs):
        dx, dyy = rotate(-size, dy, facing)
        ins.append((loc[0] + dx, loc[1] + dyy))
    return out, ins


# --------------------------------------------------------------------------- #
# netlist
# --------------------------------------------------------------------------- #
class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


class Circuit:
    def __init__(self, elem):
        self.name = elem.get("name")
        self.uf = UF()
        self.gates = []          # (kind, out_coord, [in_coords])
        self.inputs = []         # (label, coord)
        self.outputs = []        # (label, coord)
        self.problems = []
        self._ports = []
        endpoints = set()

        for w in elem.findall("wire"):
            a, b = parse_loc(w.get("from")), parse_loc(w.get("to"))
            self.uf.union(a, b)
            endpoints.add(a)
            endpoints.add(b)

        for comp in elem.findall("comp"):
            name = comp.get("name")
            attrs = {x.get("name"): x.get("val") for x in comp.findall("a")}
            loc = parse_loc(comp.get("loc"))
            if name == "Pin":
                lab = attrs.get("label", "?")
                if attrs.get("type") == "output" or attrs.get("output") == "true":
                    self.outputs.append((lab, loc))
                else:
                    self.inputs.append((lab, loc))
                w = int(attrs.get("width", "1"))
                if w != 1:
                    self.problems.append("pin %s is %d bits wide; this checker "
                                         "handles 1-bit pins" % (lab, w))
            elif name in GATES:
                facing = attrs.get("facing", "east")
                size = int(attrs.get("size", "30"))
                nin = 1 if name in ("NOT Gate", "Buffer") else int(attrs.get("inputs", "5"))
                if name in ("NOT Gate", "Buffer"):
                    dx, dy = rotate(-size, 0, facing)
                    out, ins = loc, [(loc[0] + dx, loc[1] + dy)]
                else:
                    out, ins = gate_ports(loc, facing, size, nin)
                self.gates.append((name, out, ins))
                self._ports.append((name, loc, ins + [out]))
            elif name in ("Text", "Tunnel", "Constant", "Splitter", "Probe"):
                if name in ("Tunnel", "Splitter"):
                    self.problems.append("%s present; this checker only handles "
                                         "gates, pins and wires" % name)
            # anything else is ignored (labels, etc.)

        # A port is connected if it meets a wire endpoint OR touches another
        # component's port directly (Logisim joins coincident ports).
        touch = set(endpoints)
        for _, loc in self.inputs + self.outputs:
            touch.add(loc)
        for _, _, ports in self._ports:
            touch.update(ports)
        for name, loc, ports in self._ports:
            for c in ports:
                hits = sum(1 for p in ports if p == c)
                if c not in endpoints and list(touch).count(c) == 0:
                    self.problems.append(
                        "%s at %s: port %s is not connected to anything"
                        % (name, loc, c))
                elif c not in endpoints:
                    # touching another port directly is legal, but only if
                    # something actually drives or reads it there
                    others = [1 for _, l2 in self.inputs + self.outputs if l2 == c]
                    others += [1 for n2, l2, p2 in self._ports
                               if (n2, l2) != (name, loc) and c in p2]
                    if not others:
                        self.problems.append(
                            "%s at %s: port %s floats (no wire, no touching port)"
                            % (name, loc, c))
                del hits

    def node(self, coord):
        return self.uf.find(coord)

    def evaluate(self, values):
        """values: {input label: 0/1} -> {output label: 0/1}"""
        net = {}
        for lab, loc in self.inputs:
            net[self.node(loc)] = values[lab]

        pending = list(self.gates)
        for _ in range(len(self.gates) + 2):        # combinational: converges
            still = []
            for kind, out, ins in pending:
                vals = [net.get(self.node(c)) for c in ins]
                if any(v is None for v in vals):
                    still.append((kind, out, ins))
                    continue
                if kind == "AND Gate":
                    r = int(all(vals))
                elif kind == "NAND Gate":
                    r = int(not all(vals))
                elif kind == "OR Gate":
                    r = int(any(vals))
                elif kind == "NOR Gate":
                    r = int(not any(vals))
                elif kind == "XOR Gate":
                    r = sum(vals) % 2
                elif kind == "XNOR Gate":
                    r = 1 - (sum(vals) % 2)
                elif kind == "NOT Gate":
                    r = 1 - vals[0]
                else:                                # Buffer
                    r = vals[0]
                net[self.node(out)] = r
            pending = still
            if not pending:
                break
        if pending:
            raise RuntimeError("%d gate(s) never resolved -- the netlist has a "
                               "loop or a floating input" % len(pending))

        return {lab: net.get(self.node(loc)) for lab, loc in self.outputs}


# --------------------------------------------------------------------------- #
# reference models -- straight from the Week 1 design document
# --------------------------------------------------------------------------- #
def ref_forwarding(v):
    def bits(p):
        return [v["%s[%d]" % (p, i)] for i in range(5)]

    def eq(a, b):
        return int(bits(a) == bits(b))

    exhitA = v["EXMEM_RegWrite"] & v["EXMEM_Rd_NZ"] & eq("EXMEM_Rd", "IDEX_Rs")
    exhitB = v["EXMEM_RegWrite"] & v["EXMEM_Rd_NZ"] & eq("EXMEM_Rd", "IDEX_Rt")
    wbhitA = v["MEMWB_RegWrite"] & v["MEMWB_Rd_NZ"] & eq("MEMWB_Rd", "IDEX_Rs")
    wbhitB = v["MEMWB_RegWrite"] & v["MEMWB_Rd_NZ"] & eq("MEMWB_Rd", "IDEX_Rt")
    return {"ForwardA_bit1": exhitA,
            "ForwardA_bit0": wbhitA & (1 - exhitA),
            "ForwardB_bit1": exhitB,
            "ForwardB_bit0": wbhitB & (1 - exhitB)}


def ref_hdu(v):
    def eq(a, b):
        return int([v["%s[%d]" % (a, i)] for i in range(5)]
                   == [v["%s[%d]" % (b, i)] for i in range(5)])
    hit = (v["IFID_UsesRs"] & eq("IDEX_Rt", "IFID_Rs")) | \
          (v["IFID_UsesRt"] & eq("IDEX_Rt", "IFID_Rt"))
    return {"Stall": v["IDEX_MemRead"] & v["IDEX_Rt_NZ"] & hit}


def ref_branch(v):
    def eq(a, b):
        return int([v["%s[%d]" % (a, i)] for i in range(5)]
                   == [v["%s[%d]" % (b, i)] for i in range(5)])
    match_ex = v["IDEX_RegWrite"] & v["IDEX_Rd_NZ"] & \
        (eq("IDEX_Rd", "IFID_Rs") | eq("IDEX_Rd", "IFID_Rt"))
    match_mem_load = v["EXMEM_MemRead"] & v["EXMEM_Rd_NZ"] & \
        (eq("EXMEM_Rd", "IFID_Rs") | eq("EXMEM_Rd", "IFID_Rt"))
    bs = v["IsBranch"] & (match_ex | match_mem_load)
    fc = v["EXMEM_RegWrite"] & v["EXMEM_Rd_NZ"] & eq("EXMEM_Rd", "IFID_Rs") & (1 - bs)
    fd = v["EXMEM_RegWrite"] & v["EXMEM_Rd_NZ"] & eq("EXMEM_Rd", "IFID_Rt") & (1 - bs)
    return {"BranchStall": bs, "ForwardC": fc, "ForwardD": fd}


def ref_branch_gated(v):
    """The same unit with IsBranch ANDed into ForwardC/ForwardD, which is what
    the signal names imply and what makes the trace directly comparable with
    the golden simulator."""
    r = ref_branch(v)
    r["ForwardC"] &= v["IsBranch"]
    r["ForwardD"] &= v["IsBranch"]
    return r


REFS = {"Forwarding_Unit": [("Week 1 truth table", ref_forwarding)],
        "Hazard_Detection_Unit": [("Week 1 truth table", ref_hdu)],
        "Branch_Forward_Stall": [
            ("Week 1 equation, ForwardC/D ungated", ref_branch),
            ("IsBranch-gated variant", ref_branch_gated)]}

# register-valued input groups, so random vectors are realistic and the
# derived *_NZ flags stay consistent with the register bits
REGFIELDS = {
    "Forwarding_Unit": [("EXMEM_Rd", "EXMEM_Rd_NZ"), ("MEMWB_Rd", "MEMWB_Rd_NZ"),
                        ("IDEX_Rs", None), ("IDEX_Rt", None)],
    "Hazard_Detection_Unit": [("IDEX_Rt", "IDEX_Rt_NZ"), ("IFID_Rs", None),
                              ("IFID_Rt", None)],
    "Branch_Forward_Stall": [("IDEX_Rd", "IDEX_Rd_NZ"), ("EXMEM_Rd", "EXMEM_Rd_NZ"),
                             ("IFID_Rs", None), ("IFID_Rt", None)],
}


def random_vector(circ, rng, regpool=(0, 1, 2, 5, 8, 17, 31)):
    v = {}
    fields = REGFIELDS.get(circ.name, [])
    named = set()
    for base, nz in fields:
        r = rng.choice(regpool)
        for i in range(5):
            v["%s[%d]" % (base, i)] = (r >> i) & 1
            named.add("%s[%d]" % (base, i))
        if nz:
            v[nz] = int(r != 0)
            named.add(nz)
    for lab, _ in circ.inputs:
        if lab not in named:
            v[lab] = rng.randint(0, 1)
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--circ", required=True)
    ap.add_argument("--vectors", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=16993)
    ap.add_argument("--dump", default=None, help="print the netlist of one circuit")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    root = ET.parse(a.circ).getroot()
    circuits = [Circuit(c) for c in root.findall("circuit")]
    rng = random.Random(a.seed)
    lines = ["LOGISIM CIRCUIT CHECK  --  %s" % a.circ, "=" * 72]
    failures = 0

    for c in circuits:
        lines.append("")
        lines.append("CIRCUIT: %s" % c.name)
        lines.append("  %d gates, %d inputs, %d outputs"
                     % (len(c.gates), len(c.inputs), len(c.outputs)))
        if c.problems:
            failures += 1
            lines.append("  NETLIST PROBLEMS:")
            for p in sorted(set(c.problems))[:12]:
                lines.append("    - %s" % p)
            continue
        lines.append("  netlist extraction: every gate port lands on a wire")

        if a.dump == c.name:
            for kind, out, ins in c.gates:
                lines.append("    %-10s out=%s in=%s" % (kind, out, ins))

        models = REFS.get(c.name)
        if models is None:
            lines.append("  no reference model for this circuit -- skipped")
            continue

        vectors = [random_vector(c, rng) for _ in range(a.vectors)]
        results = {}
        for label, ref in models:
            bad = []
            for v in vectors:
                try:
                    got = c.evaluate(v)
                except RuntimeError as e:
                    bad.append(("evaluation error", str(e), v))
                    break
                want = ref(v)
                for k in want:
                    if got.get(k) != want[k]:
                        bad.append((k, "got %s want %s" % (got.get(k), want[k]), v))
            results[label] = bad

        matched = [lab for lab, b in results.items() if not b]
        tested = len(vectors)
        if matched:
            for lab in matched:
                lines.append("  RESULT: PASS -- %d random vectors match the %s"
                             % (tested, lab))
            for lab, b in results.items():
                if b and len(models) > 1:
                    lines.append("          (does not match the %s: %d differing "
                                 "vectors)" % (lab, len(b)))
            bad = []
        else:
            bad = results[models[0][0]]
        if bad:
            failures += 1
            lines.append("  RESULT: FAIL after %d vectors" % tested)
            for k, msg, v in bad[:5]:
                lines.append("    %s: %s" % (k, msg))
                lines.append("      inputs: %s"
                             % ", ".join("%s=%d" % (kk, v[kk])
                                         for kk in sorted(v) if v[kk]) or "(all zero)")


    lines.append("")
    lines.append("=" * 72)
    lines.append("OVERALL: %s" % ("PASS" if failures == 0 else
                                  "FAIL in %d circuit(s)" % failures))
    text = "\n".join(lines) + "\n"
    print(text)
    if a.report:
        open(a.report, "w").write(text)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
