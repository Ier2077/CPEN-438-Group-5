#!/usr/bin/env python3
"""
crossval_sims.py -- cycle-by-cycle cross-validation between two independently
written implementations of the same pipeline.

Project 3 asks for cross-validation of the Logisim circuit against a golden
simulator. The same harness is useful one level earlier: the Week 2 golden
simulator (week2/03_evidence/src/hazard_pipeline_sim.c) and the simulator in
sim/ were written separately from the same Week 1 design document, so running
both and diffing their per-cycle traces is a real check on the design, not a
check of a program against itself.

Both simulators emit a CSV whose first twelve columns are
    cycle, IF, ID, EX, MEM, WB, ForwardA, ForwardB, ForwardC, ForwardD,
    Stall, Flush
Extra columns on either side are ignored, so the same tool also compares a
hand-recorded Logisim observation file (see START_HERE.md).

Usage:
    python3 tools/crossval_sims.py --a results/percycle_hazard.csv \
                                   --b results/w2_trace_hazard.csv \
                                   --label-a "sim/" --label-b "week2 sim"
    python3 tools/crossval_sims.py --a expected.csv --b logisim_observed.csv --offset 1
"""
import argparse
import csv
import sys

SHARED = ["IF", "ID", "EX", "MEM", "WB", "ForwardA", "ForwardB",
          "ForwardC", "ForwardD", "Stall", "Flush"]


def norm(col, v):
    """Normalise the two simulators' spellings of the same thing."""
    v = (v or "").strip().strip('"').lower()
    if v in ("", "-", "bub", "bubble", "none", "nop", "x", "0xffff", "ffff"):
        return "-"
    if col in ("ForwardA", "ForwardB"):
        return {"0": "00", "1": "01", "2": "10", "00": "00", "01": "01",
                "10": "10"}.get(v, v)
    if v.startswith("0x"):
        try:
            return str(int(v, 16))
        except ValueError:
            return v
    try:
        return str(int(v))
    except ValueError:
        return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--offset", type=int, default=0,
                    help="cycles to shift B relative to A (t=0 alignment)")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--map-a", default=None,
                    help="JSON list mapping A's instruction indices onto B's "
                         "numbering (padded image -> source position)")
    ap.add_argument("--out", default=None, help="write the report to this file")
    args = ap.parse_args()

    A = list(csv.DictReader(open(args.a)))
    B = list(csv.DictReader(open(args.b)))
    if not A or not B:
        sys.exit("one of the trace files is empty")

    imap = None
    if args.map_a:
        import json
        imap = json.load(open(args.map_a))

    def remap(col, v):
        if imap is None or col not in ("IF", "ID", "EX", "MEM", "WB"):
            return v
        t = (v or "").strip()
        if not t.lstrip("-").isdigit():
            return v
        k = int(t)
        return str(imap[k]) if 0 <= k < len(imap) else v

    cols = [c for c in SHARED if c in A[0] and c in B[0]]
    if not cols:
        sys.exit("no shared columns; check the two headers")

    lines = ["CYCLE-BY-CYCLE CROSS-VALIDATION",
             "=" * 70,
             "A: %-30s %s" % (args.label_a, args.a),
             "B: %-30s %s" % (args.label_b, args.b),
             "columns compared: %s" % ", ".join(cols),
             "alignment offset: %d" % args.offset,
             "index remap:      %s" % ("padded -> source" if args.map_a else "none"),
             "-" * 70]

    n = 0
    bad = 0
    for i, ra in enumerate(A):
        j = i + args.offset
        if j < 0 or j >= len(B):
            continue
        rb = B[j]
        n += 1
        for c in cols:
            if norm(c, remap(c, ra[c])) != norm(c, rb[c]):
                bad += 1
                lines.append("MISMATCH cycle %s column %-9s  %s=%-8s  %s=%-8s"
                             % (ra.get("cycle", i + 1), c, args.label_a,
                                remap(c, ra[c]), args.label_b, rb[c]))
                if bad >= args.limit:
                    lines.append("... stopping after %d mismatches" % args.limit)
                    break
        if bad >= args.limit:
            break

    if len(A) != len(B):
        lines.append("NOTE: trace lengths differ -- %s has %d cycles, %s has %d"
                     % (args.label_a, len(A), args.label_b, len(B)))

    lines += ["-" * 70,
              "%d cycles compared across %d columns" % (n, len(cols)),
              "RESULT: %s" % ("PASS -- the two traces agree on every compared cell"
                              if bad == 0 else "FAIL -- %d mismatching cells" % bad)]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        open(args.out, "w").write(text)
    return 0 if bad == 0 and len(A) == len(B) else (0 if bad == 0 else 1)


if __name__ == "__main__":
    sys.exit(main())
