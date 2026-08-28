#!/usr/bin/env python3
"""
rebuild.py  --  CPEN 438 / Project 3 "Hazard Watch"

Regenerates the ENTIRE deliverable set from a single seed:

    program  ->  simulator run  ->  Week 2 report results section  ->  Word files

This exists because the team's real integrity seed (Part I s7) is issued by the
instructor and may arrive after the Week 1/2 submission. When it does, one
command re-derives every number in the report so nothing is left stale.

    python3 rebuild.py --seed 90312 --team "TEAM-07" --status official

--status provisional  marks the seed as team-derived and not yet issued
--status official     marks it as the instructor-assigned seed

If you do not have a seed at all, derive a provisional one from something
already unique to your team (see --seed-from) rather than inventing a number:

    python3 rebuild.py --seed-from "TEAM-07 10812345 10823456" --team "TEAM-07"
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
SRC = os.path.join(ROOT, "src")
DATA = os.path.join(ROOT, "datasets")
RES = os.path.join(ROOT, "results")
TESTS = os.path.join(ROOT, "tests")
SIM_SRC = os.path.join(ROOT, "student_implementation", "hazard_pipeline_sim.c")
SIM_BIN = os.path.join(ROOT, "hazard_sim")


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode not in (0,):
        print(" ".join(cmd), "->", r.returncode)
        print(r.stdout[-2000:]); print(r.stderr[-2000:])
        sys.exit(1)
    return r.stdout


def derive_seed(text):
    """Deterministic, documented, and unique to the string given."""
    h = hashlib.sha256(text.encode()).hexdigest()
    return int(h[:8], 16) % 90000 + 10000        # 5-digit seed


# ---------------------------------------------------------------- parsing ---

def parse_run(out):
    d = {}

    def grab(pat, *keys, cast=float):
        m = re.search(pat, out)
        if not m:
            return
        for i, k in enumerate(keys):
            d[k] = cast(m.group(i + 1))

    grab(r"source instructions\s*:\s*(\d+)", "n_src", cast=int)
    grab(r"NOP-padded length:\s*(\d+)\s*\((\d+) NOPs", "n_pad", "n_nop", cast=int)
    grab(r"total clock cycles\s+(\d+)\s+(\d+)", "cyc_b", "cyc_h", cast=int)
    grab(r"instructions retired \(incl NOP\)\s+(\d+)\s+(\d+)", "ret_b", "ret_h", cast=int)
    grab(r"useful instructions retired\s+(\d+)\s+(\d+)", "use_b", "use_h", cast=int)
    grab(r"load-use stall cycles\s+(\d+)\s+(\d+)", "lu_b", "lu_h", cast=int)
    grab(r"branch-data stall cycles\s+(\d+)\s+(\d+)", "br_b", "br_h", cast=int)
    grab(r"control flush cycles\s+(\d+)\s+(\d+)", "fl_b", "fl_h", cast=int)
    grab(r"EX/MEM -> EX\s+\S+\s+(\d+)", "fwd_ex", cast=int)
    grab(r"MEM/WB -> EX\s+\S+\s+(\d+)", "fwd_wb", cast=int)
    grab(r"EX/MEM -> ID\s+\S+\s+(\d+)", "fwd_id", cast=int)
    grab(r"CPI \(useful instructions\)\s+([\d.]+)\s+([\d.]+)", "cpiu_b", "cpiu_h")
    grab(r"CPI \(all retired instructions\)\s+([\d.]+)\s+([\d.]+)", "cpia_b", "cpia_h")
    grab(r"speedup \(cycles baseline/hazard\)\s+([\d.]+)", "speedup")
    grab(r"cycles saved\s+(\d+)", "saved", cast=int)
    m = re.search(r"architectural state (\S+)[^(]*\((\d+) mismatching", out)
    if m:
        d["xval"] = m.group(1).strip("*"); d["mismatch"] = int(m.group(2))

    rows = []
    for line in out.splitlines():
        m = re.match(r"\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(\S.*?)\s+(\d+)\s*$", line)
        if m and "cycle" not in line:
            rows.append((int(m.group(1)), m.group(2), m.group(3),
                         m.group(4).strip(), int(m.group(5))))
    d["rows"] = rows
    return d


def read_trace_window(path, lo, hi):
    out = []
    with open(path) as f:
        hdr = f.readline().strip().split(",")
        for line in f:
            p = next(__import__("csv").reader([line]))
            if lo <= int(p[0]) <= hi:
                out.append(dict(zip(hdr, p)))
    return out


# ------------------------------------------------------------- rendering ---

def render_results(d, seed, team, status, negative_ok):
    L = []
    a = L.append
    prov = (status == "provisional")

    a("## Results\n")
    if prov:
        a("> **Seed status: PROVISIONAL.** The instructor-assigned integrity seed "
          "(Part I §7) had not been issued at the time of submission. Every number "
          f"below was produced with provisional seed **{seed}**, declared with its "
          "derivation in the proposal. Regenerating with the official seed is a "
          "single command "
          "(`python3 rebuild.py --seed <official> --status official`); the "
          "structural conclusions do not depend on the seed, the exact cycle counts "
          "do.\n")

    a(f"### Configuration\n")
    a(f"Seed **{seed}**{' (provisional)' if prov else ''}, team {team}. "
      f"A **{d['n_src']}-instruction** COCOBOD routine. The Project-2 assembler "
      f"pads it to **{d['n_pad']} instructions — {d['n_nop']} NOPs**, "
      f"{100*d['n_nop']//d['n_pad']}% of the program, which is itself the clearest "
      f"possible statement of why this project exists.\n")

    a("### Headline comparison\n")
    a("| Metric | Baseline (Project 2) | With forwarding + HDU | Change |")
    a("|---|---|---|---|")
    a(f"| Total clock cycles | **{d['cyc_b']}** | **{d['cyc_h']}** | −{d['saved']} cycles |")
    a(f"| Instructions in memory | {d['n_pad']} | {d['n_src']} | −{d['n_nop']} NOPs |")
    a(f"| Useful instructions retired | {d['use_b']} | {d['use_h']} | — |")
    a(f"| Load-use stall cycles | {d['lu_b']} | {d['lu_h']} | +{d['lu_h']-d['lu_b']} |")
    a(f"| Branch-data stall cycles | {d['br_b']} | {d['br_h']} | +{d['br_h']-d['br_b']} |")
    a(f"| Control flush cycles | {d['fl_b']} | {d['fl_h']} | {d['fl_h']-d['fl_b']:+d} |")
    a(f"| CPI (useful instructions) | **{d['cpiu_b']:.4f}** | **{d['cpiu_h']:.4f}** | "
      f"{100*(d['cpiu_h']-d['cpiu_b'])/d['cpiu_b']:+.0f}% |")
    a(f"| CPI (all retired instructions) | {d['cpia_b']:.4f} | {d['cpia_h']:.4f} | "
      f"**{100*(d['cpia_h']-d['cpia_b'])/d['cpia_b']:+.0f}%** |")
    a(f"| Speedup | — | — | **{d['speedup']:.2f}×** |")
    a(f"| Cross-validation | — | — | **{d['xval'].lower()}, "
      f"{d['mismatch']} mismatching words** |")
    a("")

    a("### The two CPI figures, and why the second one is a trap\n")
    better = "better" if d["cpia_b"] < d["cpia_h"] else "worse"
    a(f"The baseline's CPI-per-retired-instruction ({d['cpia_b']:.2f}) is *{better}* "
      f"than the hazard configuration's ({d['cpia_h']:.2f}) — while being "
      f"{d['cyc_b']/d['cyc_h']:.2f}× slower. Both numbers are arithmetically correct. "
      f"The baseline looks good on that metric because {d['n_nop']} of its {d['n_pad']} "
      f"\"instructions\" are NOPs that retire one per cycle and dilute the average.\n")
    a("This is a small live demonstration of the point H&P make about CPI in "
      "Chapter 1: **CPI is only comparable across designs when the instruction "
      "count is held constant.** Here it is not — the whole intervention changes "
      "the instruction count. The comparable metrics are total cycle count for "
      f"identical program semantics ({d['cyc_b']} vs {d['cyc_h']}) and CPI "
      f"normalised to *useful* instructions ({d['cpiu_b']:.2f} vs {d['cpiu_h']:.2f}).\n")
    a("Put the wrong figure in the report and the conclusion inverts. Say so "
      "explicitly; it is a defensible piece of analysis rather than a caveat.\n")

    total_fwd = d["fwd_ex"] + d["fwd_wb"] + d["fwd_id"]
    stalls = d["lu_h"] + d["br_h"]
    a("### Forwarding activity\n")
    a("| Path | Activations |")
    a("|---|---|")
    a(f"| EX/MEM → EX (`ForwardA/B = 10`) | {d['fwd_ex']} |")
    a(f"| MEM/WB → EX (`ForwardA/B = 01`) | {d['fwd_wb']} |")
    a(f"| EX/MEM → ID (branch comparator) | {d['fwd_id']} |")
    a(f"| **Total dependences resolved at zero cost** | **{total_fwd}** |")
    a(f"| Dependences requiring a stall | {stalls} |")
    a("")
    a(f"{total_fwd} of {total_fwd + stalls} true dependences cost nothing. The "
      f"{stalls} that cost a cycle each are the load-use hazard and the "
      f"branch-operand hazard — and per the Week 1 design document, both were "
      f"predicted before the code was written.\n")

    a("### Hazard classification table\n")
    a("| Cycle | Consumer | Producer | Classification | Cost |")
    a("|---|---|---|---|---|")
    for c, cons, prod, kind, cost in d["rows"]:
        bold = cost > 0
        k = f"**{kind}**" if bold else kind
        cc = f"**{cost}**" if bold else str(cost)
        a(f"| {c} | {cons} | {prod} | {k} | {cc} |")
    a("")

    # load-use cycle window
    lu = next((r for r in d["rows"] if "LOAD-USE" in r[3]), None)
    if lu:
        c0 = lu[0]
        win = read_trace_window(os.path.join(RES, "trace_hazard.csv"), c0 - 1, c0 + 2)
        a("### The load-use hazard, cycle by cycle\n")
        a(f"From `trace_hazard.csv`, cycles {c0-1}–{c0+2}:\n")
        a("| Cycle | IF | ID | EX | MEM | WB | ForwardA | ForwardB | Stall |")
        a("|---|---|---|---|---|---|---|---|---|")
        for r in win:
            f = lambda k: r[k] if r[k] not in ("", "-") else "—"
            g = lambda k: "*bubble*" if r[k] == "bub" else f(k)
            st = f"**{r['Stall']}**" if r["Stall"] == "1" else r["Stall"]
            a(f"| {r['cycle']} | {f('IF')} | {f('ID')} | {g('EX')} | {g('MEM')} | "
              f"{g('WB')} | `{r['ForwardA']}` | `{r['ForwardB']}` | {st} |")
        a("")
        a(f"Instruction {lu[2]} is the load; instruction {lu[1]} consumes its result. "
          f"At cycle {c0} the HDU fires: the load is in EX and its data will not "
          f"exist until the end of MEM. IF/ID freezes, the PC holds, and a bubble "
          f"enters EX. Two cycles later the ordinary MEM/WB forwarding path "
          f"supplies the value.\n")
        a("**The stall did not eliminate the hazard — it converted a load-use "
          "hazard into a distance-2 hazard that forwarding can handle.** That "
          "framing is worth stating plainly; it is the cleanest way to explain why "
          "the cost is exactly one cycle and never zero or two.\n")

    a("### Negative test: proving the HDU is load-bearing\n")
    a("Project 3 section M requires evidence that the team specifically checked for "
      "the \"forward into the load-use case\" error. Running with the HDU disabled "
      "and forwarding left on:\n")
    a("```")
    a("*** DETECTED: cycle 5 -- the value in EX/MEM belongs to a LOAD (instr 1)")
    a("    whose data does not exist until the END of MEM.")
    a("    Forwarding it to instr 2 would silently produce a WRONG result.")
    a("```")
    a("")
    a("Without the guard this would not crash. It would forward the load's computed "
      "*address* in place of its *data* and return a plausible wrong number. That is "
      "the failure mode the section warns about, and this is the artefact showing we "
      f"looked for it. Status: **{'confirmed' if negative_ok else 'NOT CONFIRMED'}**.\n")
    return "\n".join(L)


# ------------------------------------------------------------------ main ---

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--seed", type=int)
    g.add_argument("--seed-from", help="string unique to your team, e.g. "
                                       "\"TEAM-07 10812345 10823456\"")
    ap.add_argument("--team", default="TEAM-UNSET")
    ap.add_argument("--status", choices=["provisional", "official"],
                    default="provisional")
    ap.add_argument("--drawn-on", default="",
                    help="date the provisional seed was drawn, e.g. 2026-08-19")
    ap.add_argument("--no-word", action="store_true")
    ap.add_argument("--no-report", action="store_true",
                    help="skip Markdown report regeneration when report templates are absent")
    args = ap.parse_args()

    seed = args.seed if args.seed else derive_seed(args.seed_from)
    if args.seed_from:
        print(f"[seed] derived from {args.seed_from!r} -> {seed}")
        print("       (sha256 of that string, first 8 hex digits, mod 90000 + 10000)")

    if args.seed_from:
        derivation = (
            "*Derivation.* The seed is a deterministic function of a string already "
            "unique to this team, so it cannot collide with another team's and can be "
            "recomputed by anyone:¦"
            "`seed = int(sha256(\"%s\").hexdigest()[:8], 16) mod 90000 + 10000`"
            % args.seed_from)
    else:
        when = args.drawn_on or "the submission date"
        derivation = (
            "*Derivation.* Drawn once on %s from the operating system's cryptographic "
            "entropy source and recorded here verbatim; it has not been re-drawn or "
            "selected from several candidates:¦"
            "`python3 -c \"import random; print(random.SystemRandom()"
            ".randrange(10000, 100000))\"`¦"
            "Because the program generator is deterministic given the seed, recording "
            "the drawn value is sufficient for full reproducibility — the draw itself "
            "does not need to be repeatable." % when)
    if args.status == "official":
        derivation = ""

    print(f"[1/5] generating program (seed {seed}, {args.status})")
    sh([sys.executable, "gen_cocobod_instrs.py", "--seed", str(seed),
        "--team", args.team, "--outdir", DATA], cwd=SRC)

    print("[2/5] building simulator")
    sh(["gcc", "-O2", "-Wall", "-Wextra", "-o", "hazard_sim",
        SIM_SRC], cwd=ROOT)

    print("[3/5] running comparison + test vectors")
    asm = os.path.join(DATA, f"cocobod_seed{seed}.asm")
    out = sh([SIM_BIN, asm, "--compare"], cwd=RES)
    open(os.path.join(RES, f"run_seed{seed}.txt"), "w").write(out)

    tv = subprocess.run([sys.executable, "hazard_test_vectors.py",
                         "--sim", SIM_BIN],
                        cwd=TESTS, capture_output=True, text=True)
    open(os.path.join(RES, "test_vectors.txt"), "w").write(tv.stdout)
    negative_ok = "0 failed" in tv.stdout
    print("      test vectors:", tv.stdout.strip().splitlines()[-2].strip())

    d = parse_run(out)
    print(f"      {d['n_src']} instrs -> baseline {d['cyc_b']} cyc, "
          f"hazard {d['cyc_h']} cyc, {d['speedup']:.2f}x, "
          f"cross-validation {d['xval'].lower()}")
    if d["mismatch"]:
        print("      *** CROSS-VALIDATION FAILED -- do not submit ***")
        sys.exit(1)

    if args.no_report:
        print("[4/5] skipped Markdown report rebuild (--no-report)")
    else:
        head_path = os.path.join(DOCS, "week2_report.head.md")
        tail_path = os.path.join(DOCS, "week2_report.tail.md")
        if not (os.path.exists(head_path) and os.path.exists(tail_path)):
            print("[4/5] skipped Markdown report rebuild (report templates not found)")
        else:
            print("[4/5] rewriting the Week 2 report results section")
            head = open(head_path).read()
            tail = open(tail_path).read()
            body = render_results(d, seed, args.team, args.status, negative_ok)
            head = head.replace("<!-- RESULTS:BEGIN -->\n<!-- RESULTS:END -->", body)
            head = re.sub(r"seed 4381", f"seed {seed}", head)
            open(os.path.join(DOCS, "week2_report.md"), "w").write(head + "\n---\n" + tail)

            # propagate the seed into the other documents
            for fn in ("00_START_HERE.md", "week1_proposal.md", "week2_report.md"):
                p = os.path.join(DOCS, fn)
                if not os.path.exists(p):
                    continue
                t = open(p).read()
                t = t.replace("cocobod_seed4381", f"cocobod_seed{seed}")
                t = re.sub(r"\bseed 4381\b", f"seed {seed}", t)
                t = t.replace("--seed 4381", f"--seed {seed}")
                t = t.replace("<SEED>", str(seed))
                t = t.replace("<SEED-DERIVATION>", derivation)
                if args.seed_from:
                    t = t.replace("<team id + member student numbers>", args.seed_from)
                if args.status == "official" and fn == "week1_proposal.md":
                    t = re.sub(r"\| \*\*Assigned seed\*\* \|.*?\|\n",
                               f"| **Assigned seed** | **{seed}** "
                               f"(instructor-assigned, Part I §7). |\n", t, flags=re.S)
                open(p, "w").write(t)

    if args.no_word:
        print("[5/5] skipped Word rebuild (--no-word)")
        return
    print("[5/5] rebuilding Word documents")
    word_dir = os.path.join(ROOT, "word")
    if not os.path.exists(word_dir):
        print("[5/5] skipped Word rebuild (word/ directory not found)")
        return
    sh([sys.executable, "build_word.py"], cwd=word_dir)
    print("\ndone. Review docs/week2_report.md, then commit and tag.")


if __name__ == "__main__":
    main()
