/* ============================================================================
 * pipeline_core.h  --  GhanaCore-5 cycle-accurate five-stage pipeline engine
 * CPEN 438 / CPEN 315  Project 3: Hazard Watch
 *
 * Single-header engine shared by:
 *    demo_hazard_pipeline.c   (Level 1 two-hazard demonstration)
 *    hazard_pipeline.c        (Level 2/3 golden simulator, full COCOBOD routine)
 *
 * Pipeline model (Hennessy & Patterson Ch. 3 / 4):
 *   IF  ID  EX  MEM  WB
 *   - Register file writes in the FIRST half of the cycle, reads in the SECOND
 *     half  => a WB in cycle c is visible to an ID in cycle c.
 *   - Branches are resolved in ID with an early comparator; a taken branch
 *     flushes exactly one instruction (the one fetched in IF that cycle).
 *   - Forwarding unit: EX/MEM -> EX (code 10) and MEM/WB -> EX (code 01),
 *     "most recent producer wins" priority.
 *   - Hazard-detection unit (HDU): one-cycle stall on load-use.
 *   - Branch hazard unit (BHU): stalls the ID-stage comparator when an operand
 *     is still in flight, and forwards EX/MEM ALU results into the comparator.
 * ==========================================================================*/
#ifndef PIPELINE_CORE_H
#define PIPELINE_CORE_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define IMEM_WORDS  1024
#define DMEM_WORDS  4096
#define NREG        32
#define MAX_CYCLES  500000

/* ---------------- opcodes / functs ---------------------------------------*/
enum { OP_RTYPE = 0x00, OP_ADDI = 0x08, OP_LW = 0x23, OP_SW = 0x2B,
       OP_BEQ   = 0x04, OP_BNE  = 0x05, OP_HALT = 0x3F };
enum { F_ADD = 0x20, F_SUB = 0x22, F_AND = 0x24, F_OR = 0x25, F_SLT = 0x2A };

/* ---------------- decoded instruction ------------------------------------*/
typedef struct {
    uint32_t raw;
    int op, rs, rt, rd, funct;
    int32_t imm;
    int usesRs, usesRt;
    int regWrite, memRead, memWrite, memToReg, aluSrc;
    int isBranch, branchNE, isNop, isHalt;
    int dest;
    char mnem[24];
} Dec;

static inline Dec decode_instr(uint32_t w)
{
    Dec d;
    memset(&d, 0, sizeof(d));
    d.raw   = w;
    d.op    = (w >> 26) & 0x3F;
    d.rs    = (w >> 21) & 0x1F;
    d.rt    = (w >> 16) & 0x1F;
    d.rd    = (w >> 11) & 0x1F;
    d.funct =  w        & 0x3F;
    d.imm   = (int32_t)(int16_t)(w & 0xFFFF);

    if (w == 0u) { d.isNop = 1; snprintf(d.mnem, sizeof d.mnem, "NOP"); return d; }

    switch (d.op) {
    case OP_RTYPE:
        d.usesRs = d.usesRt = 1; d.regWrite = 1; d.dest = d.rd;
        switch (d.funct) {
        case F_ADD: snprintf(d.mnem, sizeof d.mnem, "ADD  R%d,R%d,R%d", d.rd, d.rs, d.rt); break;
        case F_SUB: snprintf(d.mnem, sizeof d.mnem, "SUB  R%d,R%d,R%d", d.rd, d.rs, d.rt); break;
        case F_AND: snprintf(d.mnem, sizeof d.mnem, "AND  R%d,R%d,R%d", d.rd, d.rs, d.rt); break;
        case F_OR:  snprintf(d.mnem, sizeof d.mnem, "OR   R%d,R%d,R%d", d.rd, d.rs, d.rt); break;
        case F_SLT: snprintf(d.mnem, sizeof d.mnem, "SLT  R%d,R%d,R%d", d.rd, d.rs, d.rt); break;
        default:    snprintf(d.mnem, sizeof d.mnem, "R?%02x", d.funct);
        }
        break;
    case OP_ADDI:
        d.usesRs = 1; d.regWrite = 1; d.aluSrc = 1; d.dest = d.rt;
        snprintf(d.mnem, sizeof d.mnem, "ADDI R%d,R%d,%d", d.rt, d.rs, d.imm); break;
    case OP_LW:
        d.usesRs = 1; d.regWrite = 1; d.aluSrc = 1; d.memRead = 1; d.memToReg = 1; d.dest = d.rt;
        snprintf(d.mnem, sizeof d.mnem, "LW   R%d,%d(R%d)", d.rt, d.imm, d.rs); break;
    case OP_SW:
        d.usesRs = 1; d.usesRt = 1; d.aluSrc = 1; d.memWrite = 1;
        snprintf(d.mnem, sizeof d.mnem, "SW   R%d,%d(R%d)", d.rt, d.imm, d.rs); break;
    case OP_BEQ:
        d.usesRs = d.usesRt = 1; d.isBranch = 1;
        snprintf(d.mnem, sizeof d.mnem, "BEQ  R%d,R%d,%d", d.rs, d.rt, d.imm); break;
    case OP_BNE:
        d.usesRs = d.usesRt = 1; d.isBranch = 1; d.branchNE = 1;
        snprintf(d.mnem, sizeof d.mnem, "BNE  R%d,R%d,%d", d.rs, d.rt, d.imm); break;
    case OP_HALT:
        d.isHalt = 1; snprintf(d.mnem, sizeof d.mnem, "HALT"); break;
    default:
        snprintf(d.mnem, sizeof d.mnem, "??op%02x", d.op);
    }
    return d;
}

/* ---------------- pipeline registers -------------------------------------*/
typedef struct { int valid; uint32_t pc, instr; } IFID_t;
typedef struct { int valid; uint32_t pc; Dec d; int32_t rsval, rtval; } IDEX_t;
typedef struct { int valid; uint32_t pc; Dec d; int32_t alu, rtval; int dest; } EXMEM_t;
typedef struct { int valid; uint32_t pc; Dec d; int32_t alu, memdata; int dest; } MEMWB_t;

/* ---------------- machine ------------------------------------------------*/
typedef struct {
    uint32_t imem[IMEM_WORDS];
    int32_t  dmem[DMEM_WORDS];
    int32_t  reg[NREG];
    int      nwords;
    uint32_t pc;

    int fwd_en;               /* forwarding unit enabled                    */
    int hdu_en;               /* hazard-detection + branch hazard unit      */

    IFID_t  ifid;  IDEX_t  idex;  EXMEM_t exmem; MEMWB_t memwb;

    long cycles, retired, useful;
    long stall_loaduse, stall_branch, flushes;
    long fwdA_exmem, fwdA_memwb, fwdB_exmem, fwdB_memwb;
    long fwd_branch;
    long branches_taken, branches_ntaken;

    int  allow_misforward;   /* downgrade the assert to a warning */
    FILE *trace;
    FILE *hazlog;
    FILE *csv;      /* machine-readable per-cycle log for cross-validation */
    FILE *unitcsv;  /* per-cycle INPUTS to the three hazard units, plus the
                     * outputs the simulator computed, so the Logisim
                     * subcircuits can be driven with identical stimulus */
    int   halted;
} Machine;

static inline void mach_init(Machine *m, int fwd_en, int hdu_en)
{
    memset(m, 0, sizeof(*m));
    m->fwd_en = fwd_en;
    m->hdu_en = hdu_en;
}

/* Week 1 convention (tests/hazard_test_vectors.py):
 *     MEM[word i] = (37*i + 11) % 1000
 * so every hand-derived load value is deterministic and checkable on paper. */
static inline void mach_init_dmem_pattern(Machine *m)
{
    for (int i = 0; i < DMEM_WORDS; i++) m->dmem[i] = (37 * i + 11) % 1000;
}

/* load a hex image: one 8-hex-digit word per line, '#' starts a comment */
static inline int mach_load_hex(Machine *m, const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return -1; }
    char line[512];
    int n = 0;
    while (fgets(line, sizeof line, f)) {
        char *h = strchr(line, '#'); if (h) *h = 0;
        h = strchr(line, ';'); if (h) *h = 0;   /* Week 1 asm uses ; comments */
        char *p = line;
        while (*p && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) p++;
        if (!*p) continue;
        if (!strncmp(p, "v2.0", 4) || !strncmp(p, "v3.0", 4)) continue;
        char *tok = strtok(p, " \t\r\n");
        while (tok) {                      /* Logisim images pack many per line */
            unsigned v = 0;
            if (sscanf(tok, "%x", &v) == 1) {
                if (n >= IMEM_WORDS) { fprintf(stderr, "program too big\n"); break; }
                m->imem[n++] = (uint32_t)v;
            }
            tok = strtok(NULL, " \t\r\n");
        }
    }
    fclose(f);
    m->nwords = n;
    return n;
}

static inline int32_t alu_exec(int funct, int32_t a, int32_t b)
{
    switch (funct) {
    case F_ADD: return a + b;
    case F_SUB: return a - b;
    case F_AND: return a & b;
    case F_OR:  return a | b;
    case F_SLT: return (a < b) ? 1 : 0;
    default:    return 0;
    }
}

static inline int32_t wb_value(const MEMWB_t *w)
{
    return w->d.memToReg ? w->memdata : w->alu;
}

static inline const char *slot_name(int valid, const Dec *d, char *buf, size_t n)
{
    if (!valid) { snprintf(buf, n, "%-18s", "--"); return buf; }
    snprintf(buf, n, "%-18s", d->mnem);
    return buf;
}

/* --------------------------------------------------------------------------
 * One clock cycle.  Returns 1 while the machine is still running.
 * ------------------------------------------------------------------------*/
static inline int mach_step(Machine *m)
{
    /* A program may end with an explicit HALT or simply run off the end of the
     * instruction memory (the Week 1 vectors do the latter). Either way the
     * machine is finished once nothing can be fetched and the pipeline drains. */
    int nothing_to_fetch = m->halted || ((int)(m->pc >> 2) >= m->nwords);
    if (nothing_to_fetch && !m->ifid.valid && !m->idex.valid
        && !m->exmem.valid && !m->memwb.valid)
        return 0;

    IFID_t  cur_ifid  = m->ifid;
    IDEX_t  cur_idex  = m->idex;
    EXMEM_t cur_exmem = m->exmem;
    MEMWB_t cur_memwb = m->memwb;

    IFID_t  n_ifid;  IDEX_t  n_idex;  EXMEM_t n_exmem; MEMWB_t n_memwb;
    memset(&n_ifid, 0, sizeof n_ifid);
    memset(&n_idex, 0, sizeof n_idex);
    memset(&n_exmem, 0, sizeof n_exmem);
    memset(&n_memwb, 0, sizeof n_memwb);

    int fwdA = 0, fwdB = 0;              /* 0 = ID/EX, 2 = EX/MEM, 1 = MEM/WB */
    int fwdC = 0, fwdD = 0;              /* EX/MEM -> ID branch comparator      */
    int did_stall = 0, stall_kind = 0;   /* 1 = load-use, 2 = branch          */
    int did_flush = 0;
    uint32_t branch_target = 0;
    int branch_taken = 0;

    /* ---------------- WB (first half of the cycle) ------------------------*/
    if (cur_memwb.valid) {
        if (cur_memwb.d.regWrite && cur_memwb.dest != 0)
            m->reg[cur_memwb.dest] = wb_value(&cur_memwb);
        m->retired++;
        if (!cur_memwb.d.isNop) m->useful++;
    }

    /* ---------------- MEM --------------------------------------------------*/
    if (cur_exmem.valid) {
        n_memwb.valid = 1;
        n_memwb.pc    = cur_exmem.pc;
        n_memwb.d     = cur_exmem.d;
        n_memwb.alu   = cur_exmem.alu;
        n_memwb.dest  = cur_exmem.dest;
        if (cur_exmem.d.memRead) {
            int idx = (cur_exmem.alu >> 2);
            if (idx < 0 || idx >= DMEM_WORDS) idx = 0;
            n_memwb.memdata = m->dmem[idx];
        }
        if (cur_exmem.d.memWrite) {
            int idx = (cur_exmem.alu >> 2);
            if (idx >= 0 && idx < DMEM_WORDS) m->dmem[idx] = cur_exmem.rtval;
        }
    }

    /* ---------------- EX  (the forwarding unit lives here) -----------------*/
    if (cur_idex.valid) {
        const Dec *d = &cur_idex.d;

        if (m->fwd_en) {
            /* --- ForwardA ------------------------------------------------ */
            if (cur_exmem.valid && cur_exmem.d.regWrite && cur_exmem.dest != 0
                && d->usesRs && cur_exmem.dest == d->rs)
                fwdA = 2;      /* EXhitA -- Week 1 design doc, section 3      */
            else if (cur_memwb.valid && cur_memwb.d.regWrite && cur_memwb.dest != 0
                     && d->usesRs && cur_memwb.dest == d->rs)
                fwdA = 1;
            /* --- ForwardB ------------------------------------------------ */
            if (cur_exmem.valid && cur_exmem.d.regWrite && cur_exmem.dest != 0
                && d->usesRt && cur_exmem.dest == d->rt)
                fwdB = 2;      /* EXhitB                                      */
            else if (cur_memwb.valid && cur_memwb.d.regWrite && cur_memwb.dest != 0
                     && d->usesRt && cur_memwb.dest == d->rt)
                fwdB = 1;
        }

        /* --- the guard the Week 1 design document requires -----------------
         * "There is no row for EX/MEM holds a load ... the hazard-detection
         *  unit exists precisely so this row can never be reached, and the
         *  simulator asserts on it."  If we get here the HDU has failed.    */
        if ((fwdA == 2 || fwdB == 2) && cur_exmem.d.memRead) {
            fprintf(stderr,
                "MIS-FORWARD DETECTED at cycle %ld: EX/MEM holds LW to R%d "
                "(pc=0x%04x) and the forwarding unit selected it for the "
                "instruction at pc=0x%04x. The EX/MEM latch holds that load's "
                "ADDRESS, not its data. A load-use hazard reached EX without a "
                "stall: the hazard-detection unit is disabled or broken.\n",
                m->cycles + 1, cur_exmem.dest, cur_exmem.pc, cur_idex.pc);
            if (!m->allow_misforward) exit(2);
            fprintf(stderr, "  (--allow-mis-forward given: continuing so the "
                            "silently wrong result can be shown)\n");
        }

        int32_t opA = cur_idex.rsval, opBreg = cur_idex.rtval;
        if (fwdA == 2) opA    = cur_exmem.alu;
        if (fwdA == 1) opA    = wb_value(&cur_memwb);
        if (fwdB == 2) opBreg = cur_exmem.alu;
        if (fwdB == 1) opBreg = wb_value(&cur_memwb);

        if (fwdA == 2)      m->fwdA_exmem++;
        else if (fwdA == 1) m->fwdA_memwb++;
        if (fwdB == 2)      m->fwdB_exmem++;
        else if (fwdB == 1) m->fwdB_memwb++;

        if (m->hazlog) {
            if (fwdA) fprintf(m->hazlog,
                "cycle %ld  FORWARD  ForwardA=%s  producer_pc=0x%04x -> consumer_pc=0x%04x  reg=R%d\n",
                m->cycles + 1, fwdA == 2 ? "EX/MEM(10)" : "MEM/WB(01)",
                fwdA == 2 ? cur_exmem.pc : cur_memwb.pc, cur_idex.pc, d->rs);
            if (fwdB) fprintf(m->hazlog,
                "cycle %ld  FORWARD  ForwardB=%s  producer_pc=0x%04x -> consumer_pc=0x%04x  reg=R%d\n",
                m->cycles + 1, fwdB == 2 ? "EX/MEM(10)" : "MEM/WB(01)",
                fwdB == 2 ? cur_exmem.pc : cur_memwb.pc, cur_idex.pc, d->rt);
        }

        int32_t opB = d->aluSrc ? d->imm : opBreg;

        n_exmem.valid = 1;
        n_exmem.pc    = cur_idex.pc;
        n_exmem.d     = *d;
        n_exmem.dest  = d->dest;
        n_exmem.rtval = opBreg;                    /* store data (forwarded) */

        if (d->op == OP_RTYPE)                     n_exmem.alu = alu_exec(d->funct, opA, opB);
        else if (d->op == OP_ADDI || d->op == OP_LW || d->op == OP_SW)
                                                   n_exmem.alu = opA + opB;
        else                                       n_exmem.alu = 0;
    }

    /* ---------------- ID  (decode, RF read, branch resolve, HDU/BHU) -------*/
    if (cur_ifid.valid) {
        Dec d = decode_instr(cur_ifid.instr);

        /* ---- hazard-detection unit: load-use ---------------------------- */
        if (m->hdu_en && cur_idex.valid && cur_idex.d.memRead && cur_idex.d.dest != 0) {
            if ((d.usesRs && cur_idex.d.dest == d.rs) ||
                (d.usesRt && cur_idex.d.dest == d.rt)) {
                did_stall = 1; stall_kind = 1;
            }
        }

        /* ---- branch hazard unit ----------------------------------------- */
        if (!did_stall && d.isBranch && m->hdu_en) {
            if (cur_idex.valid && cur_idex.d.regWrite && cur_idex.d.dest != 0 &&
                ((d.usesRs && cur_idex.d.dest == d.rs) || (d.usesRt && cur_idex.d.dest == d.rt))) {
                did_stall = 1; stall_kind = 2;      /* producer still in EX  */
            } else if (cur_exmem.valid && cur_exmem.d.regWrite && cur_exmem.dest != 0 &&
                       cur_exmem.d.memRead &&
                       ((d.usesRs && cur_exmem.dest == d.rs) || (d.usesRt && cur_exmem.dest == d.rt))) {
                did_stall = 1; stall_kind = 2;      /* load still in MEM     */
            } else if (cur_exmem.valid && cur_exmem.d.regWrite && cur_exmem.dest != 0 &&
                       !cur_exmem.d.memRead) {
                if (d.usesRs && cur_exmem.dest == d.rs) fwdC = 1;
                if (d.usesRt && cur_exmem.dest == d.rt) fwdD = 1;
            }
        }

        if (did_stall) {
            if (stall_kind == 1) m->stall_loaduse++; else m->stall_branch++;
            if (m->hazlog)
                fprintf(m->hazlog, "cycle %ld  STALL    %s at pc=0x%04x (bubble inserted into ID/EX)\n",
                        m->cycles + 1, stall_kind == 1 ? "LOAD-USE " : "BRANCH   ", cur_ifid.pc);
            n_ifid = cur_ifid;          /* freeze IF/ID, PC held below */
        } else {
            int32_t rsv = m->reg[d.rs];
            int32_t rtv = m->reg[d.rt];

            if (d.isBranch) {
                int32_t bs = fwdC ? cur_exmem.alu : rsv;
                int32_t bt = fwdD ? cur_exmem.alu : rtv;
                if (fwdC || fwdD) {
                    m->fwd_branch++;
                    if (m->hazlog)
                        fprintf(m->hazlog,
                            "cycle %ld  FORWARD  ForwardC/D (branch comparator <- EX/MEM)  producer_pc=0x%04x -> pc=0x%04x\n",
                            m->cycles + 1, cur_exmem.pc, cur_ifid.pc);
                }
                int eq = (bs == bt);
                branch_taken = d.branchNE ? !eq : eq;
                branch_target = cur_ifid.pc + 4 + ((uint32_t)d.imm << 2);
                if (branch_taken) m->branches_taken++; else m->branches_ntaken++;
            }

            if (d.isHalt) m->halted = 1;

            n_idex.valid = 1;
            n_idex.pc    = cur_ifid.pc;
            n_idex.d     = d;
            n_idex.rsval = rsv;
            n_idex.rtval = rtv;
        }
    }

    /* ---------------- IF ---------------------------------------------------*/
    uint32_t fetch_pc = m->pc;
    int can_fetch = !m->halted && ((int)(fetch_pc >> 2) < m->nwords);

    if (!did_stall) {
        if (can_fetch) {
            n_ifid.valid = 1;
            n_ifid.pc    = fetch_pc;
            n_ifid.instr = m->imem[fetch_pc >> 2];
            m->pc = fetch_pc + 4;
        }
        if (branch_taken) {
            if (n_ifid.valid) { memset(&n_ifid, 0, sizeof n_ifid); did_flush = 1; m->flushes++; }
            m->pc = branch_target;
            if (m->hazlog)
                fprintf(m->hazlog, "cycle %ld  FLUSH    taken branch -> 0x%04x (1 bubble)\n",
                        m->cycles + 1, branch_target);
        }
    }

    /* --------- per-cycle stimulus for the Logisim hazard subcircuits --------
     * Exactly the signals the three units see, named as in the .circ file.  */
    if (m->unitcsv) {
        Dec did = decode_instr(cur_ifid.valid ? cur_ifid.instr : 0u);
        int isBranch = cur_ifid.valid && did.isBranch;
        fprintf(m->unitcsv,
            "%ld,"
            "%d,%d,%d,%d,%d,%d,"          /* EXMEM_Rd RegWrite MemRead, MEMWB_Rd RegWrite, IDEX_Rs */
            "%d,"                          /* IDEX_Rt */
            "%d,%d,%d,"                    /* IDEX_MemRead IDEX_RegWrite IDEX_Rd */
            "%d,%d,%d,%d,%d,"              /* IFID_Rs IFID_Rt UsesRs UsesRt IsBranch */
            "%d,%d,%d,%d,%d,%d,%d\n",     /* expected outputs */
            m->cycles + 1,
            cur_exmem.valid ? cur_exmem.dest : 0,
            cur_exmem.valid ? cur_exmem.d.regWrite : 0,
            cur_exmem.valid ? cur_exmem.d.memRead : 0,
            cur_memwb.valid ? cur_memwb.dest : 0,
            cur_memwb.valid ? cur_memwb.d.regWrite : 0,
            cur_idex.valid ? cur_idex.d.rs : 0,
            cur_idex.valid ? cur_idex.d.rt : 0,
            cur_idex.valid ? cur_idex.d.memRead : 0,
            cur_idex.valid ? cur_idex.d.regWrite : 0,
            cur_idex.valid ? cur_idex.d.dest : 0,
            cur_ifid.valid ? did.rs : 0,
            cur_ifid.valid ? did.rt : 0,
            cur_ifid.valid ? did.usesRs : 0,
            cur_ifid.valid ? did.usesRt : 0,
            isBranch,
            fwdA == 2 ? 1 : 0, fwdA == 1 ? 1 : 0,
            fwdB == 2 ? 1 : 0, fwdB == 1 ? 1 : 0,
            (did_stall && stall_kind == 1) ? 1 : 0,
            (did_stall && stall_kind == 2) ? 1 : 0,
            fwdC | (fwdD << 1));
    }

    /* ------------- machine-readable per-cycle CSV --------------------------
     * Column names match the Week 2 submission's trace CSV so the two
     * independently written simulators can be diffed column by column, and
     * extends it with the PC / ALU / write-back columns Week 3 asks for.     */
    if (m->csv) {
        char cIF[16], cID[16], cEX[16], cME[16], cWB[16];
        /* during a stall the PC is frozen, so no NEW instruction enters IF;
         * report "-" there, matching the Week 2 trace convention.          */
        snprintf(cIF, sizeof cIF, (n_ifid.valid && !did_stall) ? "%d" : "-",
                 n_ifid.pc >> 2);
        snprintf(cID, sizeof cID, cur_ifid.valid ? "%d" : "-", cur_ifid.pc >> 2);
        snprintf(cEX, sizeof cEX, cur_idex.valid ? "%d" : "-", cur_idex.pc >> 2);
        snprintf(cME, sizeof cME, cur_exmem.valid? "%d" : "-", cur_exmem.pc>> 2);
        snprintf(cWB, sizeof cWB, cur_memwb.valid? "%d" : "-", cur_memwb.pc>> 2);
        const char *fa = fwdA == 2 ? "10" : fwdA == 1 ? "01" : "00";
        const char *fb = fwdB == 2 ? "10" : fwdB == 1 ? "01" : "00";
        fprintf(m->csv,
            "%ld,%s,%s,%s,%s,%s,%s,%s,%d,%d,%d,%d,0x%04x,%d,%d,%d\n",
            m->cycles + 1, cIF, cID, cEX, cME, cWB, fa, fb, fwdC, fwdD,
            did_stall ? 1 : 0, did_flush ? 1 : 0,
            n_ifid.valid ? n_ifid.pc : 0xFFFFu,
            n_exmem.valid ? n_exmem.alu : 0,
            cur_memwb.valid && cur_memwb.d.regWrite ? cur_memwb.dest : 0,
            cur_memwb.valid && cur_memwb.d.regWrite ? wb_value(&cur_memwb) : 0);
    }

    /* ---------------- trace -------------------------------------------------*/
    if (m->trace) {
        char b1[32], b2[32], b3[32], b4[32], b5[32];
        Dec dIF = decode_instr(n_ifid.valid   ? n_ifid.instr   : 0u);
        Dec dID = decode_instr(cur_ifid.valid ? cur_ifid.instr : 0u);
        fprintf(m->trace,
            "%5ld | %s| %s| %s| %s| %s| A=%d B=%d | %-9s | %-5s\n",
            m->cycles + 1,
            slot_name(n_ifid.valid,    &dIF,         b1, sizeof b1),
            slot_name(cur_ifid.valid,  &dID,         b2, sizeof b2),
            slot_name(cur_idex.valid,  &cur_idex.d,  b3, sizeof b3),
            slot_name(cur_exmem.valid, &cur_exmem.d, b4, sizeof b4),
            slot_name(cur_memwb.valid, &cur_memwb.d, b5, sizeof b5),
            fwdA, fwdB,
            did_stall ? (stall_kind == 1 ? "STALL(LU)" : "STALL(BR)") : "-",
            did_flush ? "FLUSH" : "-");
    }

    m->ifid = n_ifid; m->idex = n_idex; m->exmem = n_exmem; m->memwb = n_memwb;
    m->cycles++;
    if (m->cycles > MAX_CYCLES) { fprintf(stderr, "cycle limit exceeded\n"); return 0; }
    return 1;
}

static inline void mach_run(Machine *m)
{
    if (m->unitcsv)
        fprintf(m->unitcsv,
            "cycle,EXMEM_Rd,EXMEM_RegWrite,EXMEM_MemRead,MEMWB_Rd,MEMWB_RegWrite,"
            "IDEX_Rs,IDEX_Rt,IDEX_MemRead,IDEX_RegWrite,IDEX_Rd,"
            "IFID_Rs,IFID_Rt,IFID_UsesRs,IFID_UsesRt,IsBranch,"
            "exp_ForwardA_bit1,exp_ForwardA_bit0,exp_ForwardB_bit1,"
            "exp_ForwardB_bit0,exp_Stall,exp_BranchStall,exp_ForwardCD\n");
    if (m->csv)
        fprintf(m->csv, "cycle,IF,ID,EX,MEM,WB,ForwardA,ForwardB,ForwardC,"
                        "ForwardD,Stall,Flush,PC_IF,ALU_EX,WB_reg,WB_value\n");
    if (m->trace)
        fprintf(m->trace,
            "cycle |        IF         |        ID         |        EX         |"
            "        MEM        |        WB         | Forward   | control   |\n"
            "------+-------------------+-------------------+-------------------+"
            "-------------------+-------------------+-----------+-----------+\n");
    while (mach_step(m)) { }
}

static inline void mach_dump_state(Machine *m, FILE *f)
{
    fprintf(f, "{\n  \"registers\": [");
    for (int i = 0; i < NREG; i++) fprintf(f, "%s%d", i ? ", " : "", m->reg[i]);
    fprintf(f, "],\n  \"memory\": {");
    int first = 1;
    for (int i = 0; i < DMEM_WORDS; i++)
        if (m->dmem[i]) { fprintf(f, "%s\"%d\": %d", first ? "" : ", ", i * 4, m->dmem[i]); first = 0; }
    fprintf(f, "}\n}\n");
}

static inline void mach_dump_summary(Machine *m, FILE *f, const char *mode)
{
    double cpi_all = m->retired ? (double)m->cycles / m->retired : 0;
    double cpi_use = m->useful  ? (double)m->cycles / m->useful  : 0;
    fprintf(f,
        "{\n"
        "  \"mode\": \"%s\",\n"
        "  \"forwarding_enabled\": %d,\n"
        "  \"hazard_units_enabled\": %d,\n"
        "  \"cycles\": %ld,\n"
        "  \"instructions_committed\": %ld,\n"
        "  \"useful_instructions\": %ld,\n"
        "  \"nops_executed\": %ld,\n"
        "  \"stalls_load_use\": %ld,\n"
        "  \"stalls_branch\": %ld,\n"
        "  \"flush_cycles\": %ld,\n"
        "  \"forwards_A_EXMEM\": %ld,\n"
        "  \"forwards_A_MEMWB\": %ld,\n"
        "  \"forwards_B_EXMEM\": %ld,\n"
        "  \"forwards_B_MEMWB\": %ld,\n"
        "  \"forwards_branch_comparator\": %ld,\n"
        "  \"branches_taken\": %ld,\n"
        "  \"branches_not_taken\": %ld,\n"
        "  \"CPI_all_instructions\": %.4f,\n"
        "  \"CPI_useful_instructions\": %.4f\n"
        "}\n",
        mode, m->fwd_en, m->hdu_en, m->cycles, m->retired, m->useful,
        m->retired - m->useful, m->stall_loaduse, m->stall_branch, m->flushes,
        m->fwdA_exmem, m->fwdA_memwb, m->fwdB_exmem, m->fwdB_memwb, m->fwd_branch,
        m->branches_taken, m->branches_ntaken, cpi_all, cpi_use);
}

static inline void mach_print_summary(Machine *m, FILE *f, const char *mode)
{
    fprintf(f, "\n================ RUN SUMMARY (%s) ================\n", mode);
    fprintf(f, "forwarding unit .............. %s\n", m->fwd_en ? "ENABLED" : "disabled");
    fprintf(f, "hazard-detection/branch unit . %s\n", m->hdu_en ? "ENABLED" : "disabled");
    fprintf(f, "total cycles ................. %ld\n", m->cycles);
    fprintf(f, "instructions committed ....... %ld  (useful %ld, NOPs %ld)\n",
            m->retired, m->useful, m->retired - m->useful);
    fprintf(f, "load-use stall cycles ........ %ld\n", m->stall_loaduse);
    fprintf(f, "branch stall cycles .......... %ld\n", m->stall_branch);
    fprintf(f, "flush (bubble) cycles ........ %ld\n", m->flushes);
    fprintf(f, "forwards  A: EX/MEM=%ld  MEM/WB=%ld   B: EX/MEM=%ld  MEM/WB=%ld  branch=%ld\n",
            m->fwdA_exmem, m->fwdA_memwb, m->fwdB_exmem, m->fwdB_memwb, m->fwd_branch);
    fprintf(f, "CPI (all committed) .......... %.4f\n",
            m->retired ? (double)m->cycles / m->retired : 0.0);
    fprintf(f, "CPI (useful instructions) .... %.4f\n",
            m->useful ? (double)m->cycles / m->useful : 0.0);
    fprintf(f, "=================================================\n");
}

#endif /* PIPELINE_CORE_H */
