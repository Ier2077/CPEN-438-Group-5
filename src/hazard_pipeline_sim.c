/* ===========================================================================
 * hazard_pipeline_sim.c   --  CPEN 438 / Project 3 "Hazard Watch"
 *
 * Golden, cycle-accurate reference simulator for the GhanaCore-5 five-stage
 * pipeline (IF-ID-EX-MEM-WB), in two configurations:
 *
 *   --mode baseline   Project-2 behaviour: NO forwarding, NO hazard-detection
 *                     unit. Correctness is guaranteed by the assembler, which
 *                     pads the program with NOPs. This is the comparison
 *                     baseline demanded by Project 3 section C.
 *
 *   --mode hazard     Project-3 behaviour: full EX/MEM and MEM/WB forwarding
 *                     to the ALU inputs, EX/MEM forwarding to the ID-stage
 *                     branch comparator, a hazard-detection unit for the
 *                     load-use case, and branch resolution in ID with a
 *                     one-cycle flush. NO NOPs are inserted.
 *
 * This program is the cross-validation ORACLE for the Logisim Evolution
 * circuit built in Week 3: every signal it prints (ForwardA, ForwardB,
 * ForwardC, ForwardD, Stall, Flush) corresponds to one wire in that circuit.
 *
 * Register-file timing convention (H&P Ch. 3 / App. C):
 *   the register file is WRITTEN in the first half of a clock cycle and READ
 *   in the second half. A value written by WB in cycle t is therefore visible
 *   to an instruction in ID in that same cycle t. This is why MEM/WB
 *   forwarding is needed for a distance-2 dependence but NOT for distance-3.
 *
 * Build:  gcc -O2 -Wall -Wextra -o hazard_sim hazard_pipeline_sim.c
 * Run:    ./hazard_sim prog.asm --mode hazard --trace trace.csv
 * ===========================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define MAX_INSTR   512
#define NREG        32
#define DMEM_WORDS  256
#define MAX_CYCLES  100000
#define MAX_EVENTS  4096

/* ------------------------------------------------------------------ ISA -- */

enum {
    OP_NOP = 0, OP_ADD, OP_SUB, OP_AND, OP_OR, OP_SLT,
    OP_ADDI, OP_LW, OP_SW, OP_BEQ, OP_BNE
};

typedef struct {
    int  op;
    int  rs, rt, rd;
    int  imm;
    int  target;        /* resolved branch target, in instruction indices  */
    int  usesRs, usesRt;
    int  regWrite;      /* writes a register?                             */
    int  wreg;          /* which register it writes (0 = none)            */
    int  memRead, memWrite, isBranch;
    int  origIndex;     /* index in the UNPADDED program (-1 for a NOP)   */
    char text[96];
} Instr;

/* ------------------------------------------------- pipeline latches ------ */

typedef struct { int valid; int pc; Instr *ins; } IFID;

typedef struct {
    int valid; int pc; Instr *ins;
    int rsval, rtval;
} IDEX;

typedef struct {
    int valid; int pc; Instr *ins;
    int aluout;         /* ALU result, or the address for LW/SW           */
    int rtval;          /* store data                                     */
} EXMEM;

typedef struct {
    int valid; int pc; Instr *ins;
    int aluout;
    int memdata;
} MEMWB;

/* ------------------------------------------------- hazard bookkeeping ---- */

typedef struct {
    int  cycle;
    int  consumer;      /* original program index of the consumer         */
    int  producer;      /* original program index of the producer, or -1  */
    char kind[40];      /* classification string                          */
    char detail[80];
    int  cost;          /* cycles of penalty attributable to this event   */
} Event;

static Event  events[MAX_EVENTS];
static int    nevents = 0;

static void log_event(int cycle, int consumer, int producer,
                      const char *kind, const char *detail, int cost)
{
    if (nevents >= MAX_EVENTS) return;
    Event *e = &events[nevents++];
    e->cycle = cycle; e->consumer = consumer; e->producer = producer;
    snprintf(e->kind,   sizeof e->kind,   "%s", kind);
    snprintf(e->detail, sizeof e->detail, "%s", detail);
    e->cost = cost;
}

/* ================================================================ PARSER == */

static char *trim(char *s)
{
    while (*s && isspace((unsigned char)*s)) s++;
    if (!*s) return s;
    char *e = s + strlen(s) - 1;
    while (e > s && isspace((unsigned char)*e)) *e-- = '\0';
    return s;
}

typedef struct { char name[32]; int index; } Label;

static void set_flags(Instr *in)
{
    in->usesRs = in->usesRt = in->regWrite = 0;
    in->memRead = in->memWrite = in->isBranch = 0;
    in->wreg = 0;

    switch (in->op) {
    case OP_NOP:
        break;
    case OP_ADD: case OP_SUB: case OP_AND: case OP_OR: case OP_SLT:
        in->usesRs = in->usesRt = 1; in->regWrite = 1; in->wreg = in->rd; break;
    case OP_ADDI:
        in->usesRs = 1;              in->regWrite = 1; in->wreg = in->rt; break;
    case OP_LW:
        in->usesRs = 1; in->memRead = 1; in->regWrite = 1; in->wreg = in->rt; break;
    case OP_SW:
        /* SW reads rt as a SOURCE (the store data) -- a classic place where
         * a careless hazard-detection unit misses a real dependence.        */
        in->usesRs = 1; in->usesRt = 1; in->memWrite = 1; break;
    case OP_BEQ: case OP_BNE:
        in->usesRs = 1; in->usesRt = 1; in->isBranch = 1; break;
    }
    if (in->wreg == 0) in->regWrite = 0;   /* R0 is hardwired to zero */
}

static int reg_of(const char *tok)
{
    while (*tok && !isdigit((unsigned char)*tok) && *tok != '-') tok++;
    return atoi(tok);
}

static int parse_asm(const char *path, Instr *prog)
{
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(1); }

    char  raw[512][256];
    int   nraw = 0;
    Label labels[64]; int nlabels = 0;
    char  line[256];

    /* ---- pass 1: strip comments, collect labels ------------------------- */
    while (fgets(line, sizeof line, f)) {
        char *p = strchr(line, ';'); if (p) *p = '\0';
        char *s = trim(line);
        if (!*s) continue;

        char *colon = strchr(s, ':');
        if (colon) {
            *colon = '\0';
            char *lab = trim(s);
            snprintf(labels[nlabels].name, sizeof labels[nlabels].name, "%s", lab);
            labels[nlabels].index = nraw;
            nlabels++;
            s = trim(colon + 1);
            if (!*s) continue;
        }
        snprintf(raw[nraw], sizeof raw[nraw], "%s", s);
        nraw++;
    }
    fclose(f);

    /* ---- pass 2: encode -------------------------------------------------- */
    for (int i = 0; i < nraw; i++) {
        Instr *in = &prog[i];
        memset(in, 0, sizeof *in);
        in->origIndex = i;
        in->target = -1;
        snprintf(in->text, sizeof in->text, "%.90s", raw[i]);

        char buf[256]; snprintf(buf, sizeof buf, "%.250s", raw[i]);
        for (char *c = buf; *c; c++) if (*c == ',') *c = ' ';

        char mnem[16], a[32], b[32], c[32];
        int n = sscanf(buf, "%15s %31s %31s %31s", mnem, a, b, c);
        for (char *q = mnem; *q; q++) *q = toupper((unsigned char)*q);

        if      (!strcmp(mnem, "NOP"))  in->op = OP_NOP;
        else if (!strcmp(mnem, "ADD"))  in->op = OP_ADD;
        else if (!strcmp(mnem, "SUB"))  in->op = OP_SUB;
        else if (!strcmp(mnem, "AND"))  in->op = OP_AND;
        else if (!strcmp(mnem, "OR"))   in->op = OP_OR;
        else if (!strcmp(mnem, "SLT"))  in->op = OP_SLT;
        else if (!strcmp(mnem, "ADDI")) in->op = OP_ADDI;
        else if (!strcmp(mnem, "LW"))   in->op = OP_LW;
        else if (!strcmp(mnem, "SW"))   in->op = OP_SW;
        else if (!strcmp(mnem, "BEQ"))  in->op = OP_BEQ;
        else if (!strcmp(mnem, "BNE"))  in->op = OP_BNE;
        else { fprintf(stderr, "line %d: unknown mnemonic '%s'\n", i, mnem); exit(1); }

        switch (in->op) {
        case OP_NOP:
            break;
        case OP_ADD: case OP_SUB: case OP_AND: case OP_OR: case OP_SLT:
            if (n < 4) { fprintf(stderr, "line %d: R-type needs 3 operands\n", i); exit(1); }
            in->rd = reg_of(a); in->rs = reg_of(b); in->rt = reg_of(c);
            break;
        case OP_ADDI:
            if (n < 4) { fprintf(stderr, "line %d: ADDI needs 3 operands\n", i); exit(1); }
            in->rt = reg_of(a); in->rs = reg_of(b); in->imm = atoi(c);
            break;
        case OP_LW: case OP_SW: {
            /* form:  LW Rt, imm(Rs)  -- 'b' holds "imm(Rs)" */
            in->rt = reg_of(a);
            char *op_paren = strchr(b, '(');
            if (!op_paren) { fprintf(stderr, "line %d: expected imm(Rs)\n", i); exit(1); }
            *op_paren = '\0';
            in->imm = atoi(b);
            in->rs  = reg_of(op_paren + 1);
            break;
        }
        case OP_BEQ: case OP_BNE: {
            in->rs = reg_of(a); in->rt = reg_of(b);
            int found = 0;
            for (int L = 0; L < nlabels; L++)
                if (!strcmp(labels[L].name, c)) { in->target = labels[L].index; found = 1; break; }
            if (!found) { fprintf(stderr, "line %d: unknown label '%s'\n", i, c); exit(1); }
            break;
        }
        }
        set_flags(in);
    }
    return nraw;
}

/* ======================================================= NOP PADDING ===== */
/*
 * The Project-2 baseline has no forwarding and no interlock, so the ASSEMBLER
 * must separate every producer from every consumer by at least three
 * instruction slots (producer writes the RF in WB at cycle p+4; a consumer at
 * slot c reads the RF in ID at cycle c+1, so we need p+4 <= c+1, i.e.
 * c >= p+3). Two NOPs per hazard, in the worst case.
 */
static int pad_with_nops(Instr *in, int n, Instr *out)
{
    int lastWrite[NREG];
    int map[MAX_INSTR];
    for (int r = 0; r < NREG; r++) lastWrite[r] = -100;
    int m = 0;

    for (int i = 0; i < n; i++) {
        int need = 0;
        if (in[i].usesRs && in[i].rs != 0 && lastWrite[in[i].rs] + 3 > need)
            need = lastWrite[in[i].rs] + 3;
        if (in[i].usesRt && in[i].rt != 0 && lastWrite[in[i].rt] + 3 > need)
            need = lastWrite[in[i].rt] + 3;

        while (m < need) {
            Instr *nop = &out[m];
            memset(nop, 0, sizeof *nop);
            nop->op = OP_NOP; nop->origIndex = -1; nop->target = -1;
            snprintf(nop->text, sizeof nop->text, "NOP           ; hazard padding");
            set_flags(nop);
            m++;
        }
        map[i] = m;
        out[m] = in[i];
        if (out[m].regWrite) lastWrite[out[m].wreg] = m;
        m++;
    }
    /* re-point branch targets at the padded positions */
    for (int i = 0; i < m; i++)
        if (out[i].isBranch && out[i].origIndex >= 0)
            out[i].target = (in[out[i].origIndex].target < n)
                            ? map[in[out[i].origIndex].target] : m;
    return m;
}

/* ============================================================ SIMULATOR == */

typedef struct {
    long cycles;
    long retired;          /* every instruction that reaches WB, NOPs included */
    long useful;           /* retired instructions that are not padding NOPs   */
    long stall_loaduse;
    long stall_branch;
    long flushes;
    long fwd_exmem;
    long fwd_memwb;
    long fwd_branch;
    int  reg[NREG];
    int  dmem[DMEM_WORDS];
} Result;

static int alu(int op, int a, int b)
{
    switch (op) {
    case OP_ADD: case OP_ADDI: case OP_LW: case OP_SW: return a + b;
    case OP_SUB: return a - b;
    case OP_AND: return a & b;
    case OP_OR:  return a | b;
    case OP_SLT: return (a < b) ? 1 : 0;
    default:     return 0;
    }
}

static Result run(Instr *prog, int n, int forwarding, int hdu, FILE *trace, int verbose)
{
    Result R; memset(&R, 0, sizeof R);
    for (int i = 0; i < DMEM_WORDS; i++) R.dmem[i] = (i * 37 + 11) % 1000;

    IFID  ifid  = {0}; IDEX  idex  = {0};
    EXMEM exmem = {0}; MEMWB memwb = {0};
    int pc = 0;

    if (trace)
        fprintf(trace, "cycle,IF,ID,EX,MEM,WB,ForwardA,ForwardB,ForwardC,ForwardD,"
                       "Stall,Flush,note\n");

    for (long cyc = 1; cyc <= MAX_CYCLES; cyc++) {

        if (!ifid.valid && !idex.valid && !exmem.valid && !memwb.valid && pc >= n)
            break;

        IFID  n_ifid  = {0}; IDEX  n_idex  = {0};
        EXMEM n_exmem = {0}; MEMWB n_memwb = {0};

        int fwdA = 0, fwdB = 0, fwdC = 0, fwdD = 0;
        int stall = 0, flush = 0;
        char note[160] = "";

        /* ---------------- WB : writes the RF in the FIRST half ----------- */
        if (memwb.valid) {
            Instr *w = memwb.ins;
            if (w->regWrite && w->wreg != 0)
                R.reg[w->wreg] = w->memRead ? memwb.memdata : memwb.aluout;
            R.retired++;
            if (w->origIndex >= 0 && w->op != OP_NOP) R.useful++;
        }

        /* ---------------- MEM -------------------------------------------- */
        if (exmem.valid) {
            Instr *m = exmem.ins;
            n_memwb.valid = 1; n_memwb.pc = exmem.pc; n_memwb.ins = m;
            n_memwb.aluout = exmem.aluout;
            if (m->memRead) {
                int a = (exmem.aluout >> 2) & (DMEM_WORDS - 1);
                n_memwb.memdata = R.dmem[a];
            } else if (m->memWrite) {
                int a = (exmem.aluout >> 2) & (DMEM_WORDS - 1);
                R.dmem[a] = exmem.rtval;
            }
        }

        /* ---------------- EX : the FORWARDING UNIT lives here ------------ */
        if (idex.valid) {
            Instr *e = idex.ins;
            int opA = idex.rsval, opB = idex.rtval;

            if (forwarding) {
                /* ForwardA: 10 = EX/MEM, 01 = MEM/WB, 00 = register file.
                 * Priority: the MOST RECENT producer wins. Getting this
                 * backwards is the single most common bug in this project. */
                if (exmem.valid && exmem.ins->regWrite && exmem.ins->wreg != 0
                    && e->usesRs && exmem.ins->wreg == e->rs) {
                    if (exmem.ins->memRead) {
                        fprintf(stderr,
                          "\n*** DETECTED: cycle %ld -- the value in EX/MEM belongs to a "
                          "LOAD (instr %d) whose data does not exist until the END of MEM.\n"
                          "    Forwarding it to instr %d would silently produce a WRONG result.\n"
                          "    This is exactly the error the hazard-detection unit exists to prevent.\n",
                          cyc, exmem.ins->origIndex, e->origIndex);
                        exit(2);
                    }
                    fwdA = 2; opA = exmem.aluout; R.fwd_exmem++;
                    log_event(cyc, e->origIndex, exmem.ins->origIndex,
                              "RAW forwarded EX/MEM->EX", "operand A (rs)", 0);
                } else if (memwb.valid && memwb.ins->regWrite && memwb.ins->wreg != 0
                           && e->usesRs && memwb.ins->wreg == e->rs) {
                    fwdA = 1;
                    opA = memwb.ins->memRead ? memwb.memdata : memwb.aluout;
                    R.fwd_memwb++;
                    log_event(cyc, e->origIndex, memwb.ins->origIndex,
                              "RAW forwarded MEM/WB->EX", "operand A (rs)", 0);
                }

                if (exmem.valid && exmem.ins->regWrite && exmem.ins->wreg != 0
                    && e->usesRt && exmem.ins->wreg == e->rt) {
                    if (exmem.ins->memRead) {
                        fprintf(stderr, "\n*** DETECTED: cycle %ld -- load result forwarded into "
                                        "operand B without a stall (instr %d -> instr %d).\n",
                                        cyc, exmem.ins->origIndex, e->origIndex);
                        exit(2);
                    }
                    fwdB = 2; opB = exmem.aluout; R.fwd_exmem++;
                    log_event(cyc, e->origIndex, exmem.ins->origIndex,
                              "RAW forwarded EX/MEM->EX", "operand B (rt)", 0);
                } else if (memwb.valid && memwb.ins->regWrite && memwb.ins->wreg != 0
                           && e->usesRt && memwb.ins->wreg == e->rt) {
                    fwdB = 1;
                    opB = memwb.ins->memRead ? memwb.memdata : memwb.aluout;
                    R.fwd_memwb++;
                    log_event(cyc, e->origIndex, memwb.ins->origIndex,
                              "RAW forwarded MEM/WB->EX", "operand B (rt)", 0);
                }
            }

            int second = (e->op == OP_ADDI || e->op == OP_LW || e->op == OP_SW)
                         ? e->imm : opB;

            n_exmem.valid = 1; n_exmem.pc = idex.pc; n_exmem.ins = e;
            n_exmem.aluout = alu(e->op, opA, second);
            n_exmem.rtval  = opB;          /* store data, post-forwarding */
        }

        /* ---------------- ID : decode, RF read, branch, HAZARD DETECTION -- */
        int branch_taken = 0, branch_target = 0;

        if (ifid.valid) {
            Instr *d = ifid.ins;
            int rsv = (d->rs == 0) ? 0 : R.reg[d->rs];
            int rtv = (d->rt == 0) ? 0 : R.reg[d->rt];

            /* ---- HAZARD-DETECTION UNIT (load-use) ----------------------- */
            if (forwarding && hdu && idex.valid && idex.ins->memRead && idex.ins->rt != 0
                && ((d->usesRs && idex.ins->rt == d->rs) ||
                    (d->usesRt && idex.ins->rt == d->rt))) {
                stall = 1;
                R.stall_loaduse++;
                snprintf(note, sizeof note,
                         "LOAD-USE stall: instr %d needs R%d from LW at instr %d",
                         d->origIndex,
                         (d->usesRs && idex.ins->rt == d->rs) ? d->rs : d->rt,
                         idex.ins->origIndex);
                log_event(cyc, d->origIndex, idex.ins->origIndex,
                          "LOAD-USE  stall required", note, 1);
            }

            /* ---- branch operand hazards + ID-stage forwarding ------------ */
            if (!stall && d->isBranch && forwarding) {
                /* producer still in EX -> its result does not exist yet     */
                if (idex.valid && idex.ins->regWrite && idex.ins->wreg != 0
                    && (idex.ins->wreg == d->rs || idex.ins->wreg == d->rt)) {
                    stall = 1; R.stall_branch++;
                    snprintf(note, sizeof note,
                             "BRANCH stall: instr %d in EX still producing R%d",
                             idex.ins->origIndex, idex.ins->wreg);
                    log_event(cyc, d->origIndex, idex.ins->origIndex,
                              "BRANCH-DATA stall required", note, 1);
                }
                /* producer is a LOAD in MEM -> data not ready until end MEM  */
                else if (exmem.valid && exmem.ins->memRead && exmem.ins->wreg != 0
                         && (exmem.ins->wreg == d->rs || exmem.ins->wreg == d->rt)) {
                    stall = 1; R.stall_branch++;
                    snprintf(note, sizeof note,
                             "BRANCH stall: LW at instr %d still in MEM",
                             exmem.ins->origIndex);
                    log_event(cyc, d->origIndex, exmem.ins->origIndex,
                              "BRANCH-DATA stall required", note, 1);
                }
                else {
                    /* EX/MEM -> ID comparator forwarding */
                    if (exmem.valid && exmem.ins->regWrite && exmem.ins->wreg != 0
                        && exmem.ins->wreg == d->rs) {
                        fwdC = 1; rsv = exmem.aluout; R.fwd_branch++;
                        log_event(cyc, d->origIndex, exmem.ins->origIndex,
                                  "RAW forwarded EX/MEM->ID", "branch operand rs", 0);
                    }
                    if (exmem.valid && exmem.ins->regWrite && exmem.ins->wreg != 0
                        && exmem.ins->wreg == d->rt) {
                        fwdD = 1; rtv = exmem.aluout; R.fwd_branch++;
                        log_event(cyc, d->origIndex, exmem.ins->origIndex,
                                  "RAW forwarded EX/MEM->ID", "branch operand rt", 0);
                    }
                }
            }

            if (!stall) {
                if (d->isBranch) {
                    branch_taken = (d->op == OP_BEQ) ? (rsv == rtv) : (rsv != rtv);
                    branch_target = d->target;
                }
                n_idex.valid = 1; n_idex.pc = ifid.pc; n_idex.ins = d;
                n_idex.rsval = rsv; n_idex.rtval = rtv;
            }
        }

        /* ---------------- IF ---------------------------------------------- */
        int fetched = -1;
        if (!stall && pc < n) {
            n_ifid.valid = 1; n_ifid.pc = pc; n_ifid.ins = &prog[pc];
            fetched = pc;
        }

        /* ---------------- control: stall / flush / sequential ------------- */
        if (stall) {
            n_ifid = ifid;                 /* freeze IF/ID and the PC        */
            /* n_idex stays invalid -> a bubble is injected into EX          */
        } else if (branch_taken) {
            flush = 1; R.flushes++;
            n_ifid.valid = 0;              /* kill the wrongly-fetched instr */
            pc = branch_target;
            if (!note[0])
                snprintf(note, sizeof note, "BRANCH TAKEN -> flush IF/ID, PC <- %d",
                         branch_target);
            log_event(cyc, ifid.ins->origIndex, -1,
                      "CONTROL flush (taken branch)", note, 1);
        } else if (pc < n) {
            pc++;
        }

        /* ---------------- trace ------------------------------------------- */
        if (trace) {
            char sIF[8], sID[8], sEX[8], sME[8], sWB[8];
            snprintf(sIF, 8, "%s", fetched >= 0 ? "" : "-");
            if (fetched >= 0) snprintf(sIF, 8, "%d", prog[fetched].origIndex);
            snprintf(sID, 8, "%s", ifid.valid  ? "" : "-");
            if (ifid.valid)  snprintf(sID, 8, "%d", ifid.ins->origIndex);
            snprintf(sEX, 8, "%s", idex.valid  ? "" : "bub");
            if (idex.valid)  snprintf(sEX, 8, "%d", idex.ins->origIndex);
            snprintf(sME, 8, "%s", exmem.valid ? "" : "bub");
            if (exmem.valid) snprintf(sME, 8, "%d", exmem.ins->origIndex);
            snprintf(sWB, 8, "%s", memwb.valid ? "" : "bub");
            if (memwb.valid) snprintf(sWB, 8, "%d", memwb.ins->origIndex);

            fprintf(trace, "%ld,%s,%s,%s,%s,%s,%02d,%02d,%d,%d,%d,%d,\"%s\"\n",
                    cyc, sIF, sID, sEX, sME, sWB,
                    fwdA == 2 ? 10 : fwdA, fwdB == 2 ? 10 : fwdB,
                    fwdC, fwdD, stall, flush, note);
        }
        if (verbose && note[0]) printf("  cycle %4ld : %s\n", cyc, note);

        ifid = n_ifid; idex = n_idex; exmem = n_exmem; memwb = n_memwb;
        R.cycles = cyc;
    }
    return R;
}

/* =================================================================== MAIN */

static void print_state(const char *tag, Result *R)
{
    printf("\n  final architectural state (%s)\n", tag);
    printf("    non-zero registers : ");
    int k = 0;
    for (int i = 1; i < NREG; i++)
        if (R->reg[i]) { printf("R%d=%d ", i, R->reg[i]); if (++k % 6 == 0) printf("\n                         "); }
    printf("\n");
}

int main(int argc, char **argv)
{
    const char *path = NULL, *tracepath = NULL, *mode = "hazard";
    int verbose = 0, both = 0, hdu = 1;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--mode") && i + 1 < argc)        mode = argv[++i];
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc)  tracepath = argv[++i];
        else if (!strcmp(argv[i], "--verbose"))                verbose = 1;
        else if (!strcmp(argv[i], "--compare"))                both = 1;
        else if (!strcmp(argv[i], "--no-hdu"))                 hdu = 0;
        else path = argv[i];
    }
    if (!path) {
        fprintf(stderr,
          "usage: %s prog.asm [--mode baseline|hazard] [--trace f.csv] "
          "[--verbose] [--compare] [--no-hdu]\n", argv[0]);
        return 1;
    }

    static Instr prog[MAX_INSTR], padded[MAX_INSTR];
    int n = parse_asm(path, prog);
    int np = pad_with_nops(prog, n, padded);

    printf("=====================================================================\n");
    printf(" GhanaCore-5 hazard simulator   --  %s\n", path);
    printf(" source instructions        : %d\n", n);
    printf(" Project-2 NOP-padded length: %d  (%d NOPs inserted by the assembler)\n",
           np, np - n);
    printf("=====================================================================\n");

    if (both) {
        FILE *tb = fopen("trace_baseline.csv", "w");
        FILE *th = fopen("trace_hazard.csv", "w");

        printf("\n--- MODE: baseline (Project 2: no forwarding, NOP-padded) -----------\n");
        Result B = run(padded, np, 0, 1, tb, verbose);
        printf("\n--- MODE: hazard  (Project 3: forwarding + HDU, zero NOPs) ----------\n");
        nevents = 0;                       /* keep only the hazard-mode events */
        Result H = run(prog, n, 1, hdu, th, verbose);
        if (tb) fclose(tb);
        if (th) fclose(th);

        int mismatch = 0;
        for (int i = 0; i < NREG; i++)  if (B.reg[i]  != H.reg[i])  mismatch++;
        for (int i = 0; i < DMEM_WORDS; i++) if (B.dmem[i] != H.dmem[i]) mismatch++;

        printf("\n=========================== RESULTS =================================\n");
        printf("                                     baseline     hazard-handling\n");
        printf("  total clock cycles              %10ld %15ld\n", B.cycles, H.cycles);
        printf("  instructions retired (incl NOP) %10ld %15ld\n", B.retired, H.retired);
        printf("  useful instructions retired     %10ld %15ld\n", B.useful, H.useful);
        printf("  load-use stall cycles           %10ld %15ld\n", B.stall_loaduse, H.stall_loaduse);
        printf("  branch-data stall cycles        %10ld %15ld\n", B.stall_branch, H.stall_branch);
        printf("  control flush cycles            %10ld %15ld\n", B.flushes, H.flushes);
        printf("  forwards via EX/MEM -> EX       %10s %15ld\n", "n/a", H.fwd_exmem);
        printf("  forwards via MEM/WB -> EX       %10s %15ld\n", "n/a", H.fwd_memwb);
        printf("  forwards via EX/MEM -> ID       %10s %15ld\n", "n/a", H.fwd_branch);
        printf("  ---------------------------------------------------------------\n");
        printf("  CPI (useful instructions)       %10.4f %15.4f\n",
               (double)B.cycles / B.useful, (double)H.cycles / H.useful);
        printf("  CPI (all retired instructions)  %10.4f %15.4f\n",
               (double)B.cycles / B.retired, (double)H.cycles / H.retired);
        printf("  speedup (cycles baseline/hazard)%26.4f\n",
               (double)B.cycles / (double)H.cycles);
        printf("  cycles saved                    %26ld\n", B.cycles - H.cycles);
        printf("  ---------------------------------------------------------------\n");
        printf("  CROSS-VALIDATION: architectural state %s (%d mismatching words)\n",
               mismatch ? "*** DIFFERS ***" : "IDENTICAL", mismatch);
        printf("=====================================================================\n");

        printf("\n---------------------- HAZARD CLASSIFICATION TABLE -------------------\n");
        printf(" %5s %9s %9s  %-30s %5s\n",
               "cycle", "consumer", "producer", "classification", "cost");
        printf(" ---------------------------------------------------------------------\n");
        for (int i = 0; i < nevents; i++)
            printf(" %5d %9d %9d  %-30s %5d\n", events[i].cycle, events[i].consumer,
                   events[i].producer, events[i].kind, events[i].cost);
        printf(" ---------------------------------------------------------------------\n");
        printf(" total events: %d\n", nevents);

        print_state("baseline", &B);
        print_state("hazard-handling", &H);

        FILE *hz = fopen("hazard_classification.csv", "w");
        if (hz) {
            fprintf(hz, "cycle,consumer_instr,producer_instr,classification,detail,cost_cycles\n");
            for (int i = 0; i < nevents; i++)
                fprintf(hz, "%d,%d,%d,\"%s\",\"%s\",%d\n", events[i].cycle,
                        events[i].consumer, events[i].producer,
                        events[i].kind, events[i].detail, events[i].cost);
            fclose(hz);
            printf("\n wrote trace_baseline.csv, trace_hazard.csv, hazard_classification.csv\n");
        }
        return mismatch ? 3 : 0;
    }

    int fwd = strcmp(mode, "baseline") != 0;
    FILE *t = tracepath ? fopen(tracepath, "w") : NULL;
    Result Rr = fwd ? run(prog, n, 1, hdu, t, verbose) : run(padded, np, 0, 1, t, verbose);
    if (t) fclose(t);
    printf("\n  cycles=%ld  useful=%ld  CPI=%.4f  stalls(lu/br)=%ld/%ld  flushes=%ld\n",
           Rr.cycles, Rr.useful, (double)Rr.cycles / Rr.useful,
           Rr.stall_loaduse, Rr.stall_branch, Rr.flushes);
    print_state(mode, &Rr);
    return 0;
}
