/* ============================================================================
 * asm_core.h -- minimal two-pass assembler for GhanaCore-5
 *
 * The Week 1 test harness (tests/hazard_test_vectors.py) invokes the simulator
 * as   hazard_sim <file.asm> --mode hazard   so the simulator must assemble
 * source itself rather than consume a pre-built hex image.
 *
 * Accepted syntax (both ';' and '#' start a comment):
 *      LABEL:                       on its own line or before an instruction
 *      ADD  rd, rs, rt              also SUB, AND, OR, SLT
 *      ADDI rt, rs, imm
 *      LW   rt, imm(rs)             also SW
 *      BEQ  rs, rt, LABEL           also BNE; a bare integer offset also works
 *      NOP
 *      HALT
 * Registers are written R0..R31, case-insensitive. Immediates may be decimal
 * (with optional sign) or 0x-prefixed hex.
 * ==========================================================================*/
#ifndef ASM_CORE_H
#define ASM_CORE_H

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "pipeline_core.h"

#define ASM_MAX_LABELS 256

typedef struct { char name[64]; int index; } AsmLabel;

static inline char *asm_trim(char *s)
{
    while (*s && isspace((unsigned char)*s)) s++;
    char *e = s + strlen(s);
    while (e > s && isspace((unsigned char)e[-1])) *--e = 0;
    return s;
}

static inline int asm_reg(const char *tok, int line)
{
    while (*tok && (isspace((unsigned char)*tok) || *tok == ',')) tok++;
    if (*tok != 'R' && *tok != 'r') {
        fprintf(stderr, "line %d: expected a register, got '%s'\n", line, tok);
        exit(3);
    }
    int n = atoi(tok + 1);
    if (n < 0 || n > 31) {
        fprintf(stderr, "line %d: register out of range: '%s'\n", line, tok);
        exit(3);
    }
    return n;
}

static inline int32_t asm_imm(const char *tok, int line)
{
    while (*tok && (isspace((unsigned char)*tok) || *tok == ',')) tok++;
    char *end = NULL;
    long v = strtol(tok, &end, 0);
    if (end == tok) {
        fprintf(stderr, "line %d: expected an immediate, got '%s'\n", line, tok);
        exit(3);
    }
    return (int32_t)v;
}

/* One parsed source line, before label resolution. */
typedef struct {
    int  kind;                 /* 0 = encodable now, 1 = branch needing a label */
    uint32_t word;
    char label[64];
    int  op, rs, rt;
    int  srcline;
} AsmItem;

/* Assemble `path` into m->imem. Returns the instruction count. */
static inline int mach_load_asm(Machine *m, const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return -1; }

    static AsmItem items[IMEM_WORDS];
    static AsmLabel labels[ASM_MAX_LABELS];
    int nitem = 0, nlab = 0, lineno = 0;
    char raw[512];

    while (fgets(raw, sizeof raw, f)) {
        lineno++;
        char *h = strchr(raw, ';'); if (h) *h = 0;
        h = strchr(raw, '#');       if (h) *h = 0;
        char *line = asm_trim(raw);
        if (!*line) continue;

        /* leading labels, possibly several, possibly followed by code */
        char *colon;
        while ((colon = strchr(line, ':')) != NULL) {
            *colon = 0;
            char *lab = asm_trim(line);
            if (*lab) {
                if (nlab >= ASM_MAX_LABELS) { fprintf(stderr, "too many labels\n"); exit(3); }
                snprintf(labels[nlab].name, sizeof labels[nlab].name, "%s", lab);
                labels[nlab].index = nitem;
                nlab++;
            }
            line = asm_trim(colon + 1);
        }
        if (!*line) continue;

        /* split into whitespace/comma separated tokens, keeping "imm(rs)" whole */
        char buf[256];
        snprintf(buf, sizeof buf, "%s", line);
        char *tok[8]; int nt = 0;
        for (char *p = strtok(buf, " \t,"); p && nt < 8; p = strtok(NULL, " \t,"))
            tok[nt++] = p;

        for (char *p = tok[0]; *p; p++) *p = (char)toupper((unsigned char)*p);
        AsmItem it; memset(&it, 0, sizeof it); it.srcline = lineno;

        if (!strcmp(tok[0], "NOP")) {
            it.word = 0;
        } else if (!strcmp(tok[0], "HALT")) {
            it.word = (uint32_t)OP_HALT << 26;
        } else if (!strcmp(tok[0], "ADD") || !strcmp(tok[0], "SUB") ||
                   !strcmp(tok[0], "AND") || !strcmp(tok[0], "OR")  ||
                   !strcmp(tok[0], "SLT")) {
            if (nt < 4) { fprintf(stderr, "line %d: R-type needs 3 registers\n", lineno); exit(3); }
            int funct = !strcmp(tok[0], "ADD") ? F_ADD :
                        !strcmp(tok[0], "SUB") ? F_SUB :
                        !strcmp(tok[0], "AND") ? F_AND :
                        !strcmp(tok[0], "OR")  ? F_OR  : F_SLT;
            int rd = asm_reg(tok[1], lineno), rs = asm_reg(tok[2], lineno),
                rt = asm_reg(tok[3], lineno);
            it.word = ((uint32_t)rs << 21) | ((uint32_t)rt << 16)
                    | ((uint32_t)rd << 11) | (uint32_t)funct;
        } else if (!strcmp(tok[0], "ADDI")) {
            if (nt < 4) { fprintf(stderr, "line %d: ADDI needs rt, rs, imm\n", lineno); exit(3); }
            int rt = asm_reg(tok[1], lineno), rs = asm_reg(tok[2], lineno);
            int32_t imm = asm_imm(tok[3], lineno);
            it.word = ((uint32_t)OP_ADDI << 26) | ((uint32_t)rs << 21)
                    | ((uint32_t)rt << 16) | (uint32_t)(imm & 0xFFFF);
        } else if (!strcmp(tok[0], "LW") || !strcmp(tok[0], "SW")) {
            if (nt < 3) { fprintf(stderr, "line %d: %s needs rt, imm(rs)\n", lineno, tok[0]); exit(3); }
            int rt = asm_reg(tok[1], lineno);
            int32_t imm; int rs;
            char *lp = strchr(tok[2], '(');
            if (lp) {
                *lp = 0;
                imm = asm_imm(tok[2], lineno);
                char *rp = strchr(lp + 1, ')');
                if (rp) *rp = 0;
                rs = asm_reg(lp + 1, lineno);
            } else {                       /* "LW R6 0 R2" form */
                if (nt < 4) { fprintf(stderr, "line %d: malformed %s\n", lineno, tok[0]); exit(3); }
                imm = asm_imm(tok[2], lineno);
                rs = asm_reg(tok[3], lineno);
            }
            int op = !strcmp(tok[0], "LW") ? OP_LW : OP_SW;
            it.word = ((uint32_t)op << 26) | ((uint32_t)rs << 21)
                    | ((uint32_t)rt << 16) | (uint32_t)(imm & 0xFFFF);
        } else if (!strcmp(tok[0], "BEQ") || !strcmp(tok[0], "BNE")) {
            if (nt < 4) { fprintf(stderr, "line %d: %s needs rs, rt, target\n", lineno, tok[0]); exit(3); }
            it.op = !strcmp(tok[0], "BEQ") ? OP_BEQ : OP_BNE;
            it.rs = asm_reg(tok[1], lineno);
            it.rt = asm_reg(tok[2], lineno);
            char *t = tok[3];
            while (*t && isspace((unsigned char)*t)) t++;
            if (isdigit((unsigned char)*t) || *t == '-' || *t == '+') {
                int32_t off = asm_imm(t, lineno);
                it.word = ((uint32_t)it.op << 26) | ((uint32_t)it.rs << 21)
                        | ((uint32_t)it.rt << 16) | (uint32_t)(off & 0xFFFF);
            } else {
                it.kind = 1;
                snprintf(it.label, sizeof it.label, "%s", t);
            }
        } else {
            fprintf(stderr, "line %d: unknown mnemonic '%s'\n", lineno, tok[0]);
            exit(3);
        }

        if (nitem >= IMEM_WORDS) { fprintf(stderr, "program too big\n"); exit(3); }
        items[nitem++] = it;
    }
    fclose(f);

    /* second pass: resolve branch labels to PC-relative word offsets */
    for (int i = 0; i < nitem; i++) {
        if (!items[i].kind) { m->imem[i] = items[i].word; continue; }
        int target = -1;
        for (int k = 0; k < nlab; k++)
            if (!strcmp(labels[k].name, items[i].label)) { target = labels[k].index; break; }
        if (target < 0) {
            fprintf(stderr, "line %d: undefined label '%s'\n", items[i].srcline, items[i].label);
            exit(3);
        }
        int32_t off = target - (i + 1);
        m->imem[i] = ((uint32_t)items[i].op << 26) | ((uint32_t)items[i].rs << 21)
                   | ((uint32_t)items[i].rt << 16) | (uint32_t)(off & 0xFFFF);
    }

    m->nwords = nitem;
    return nitem;
}

#endif /* ASM_CORE_H */
