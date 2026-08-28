/* ============================================================================
 * demo_hazard_pipeline.c -- Level 1 complete demonstration (Project 3)
 *
 * Two hazards, one of each kind that matters:
 *   (a) SUB R4,R1,R5 immediately after ADD R1,R2,R5
 *          -> pure RAW, resolved by EX/MEM forwarding, ZERO stalls
 *   (b) ADD R7,R6,R4 immediately after LW R6,0(R2)
 *          -> load-use, forwarding CANNOT fix it, EXACTLY ONE stall cycle,
 *             then the value arrives by MEM/WB forwarding
 *
 * Build: gcc -O2 -std=c11 -Wall -o demo sim/demo_hazard_pipeline.c
 * Run:   ./demo
 * ==========================================================================*/
#include "pipeline_core.h"

static uint32_t R(int funct, int rd, int rs, int rt)
{ return ((uint32_t)0 << 26) | ((uint32_t)rs << 21) | ((uint32_t)rt << 16)
       | ((uint32_t)rd << 11) | (uint32_t)funct; }
static uint32_t I(int op, int rt, int rs, int imm)
{ return ((uint32_t)op << 26) | ((uint32_t)rs << 21) | ((uint32_t)rt << 16)
       | ((uint32_t)(imm & 0xFFFF)); }

int main(void)
{
    uint32_t prog[] = {
        I(OP_ADDI, 2, 0, 40),        /* ADDI R2,R0,40      base address      */
        I(OP_ADDI, 5, 0,  7),        /* ADDI R5,R0,7                          */
        R(F_ADD,   1, 2, 5),         /* ADD  R1,R2,R5                         */
        R(F_SUB,   4, 1, 5),         /* SUB  R4,R1,R5   <-- forwarded RAW     */
        I(OP_LW,   6, 2,  0),        /* LW   R6,0(R2)                         */
        R(F_ADD,   7, 6, 4),         /* ADD  R7,R6,R4   <-- LOAD-USE, 1 stall */
        I(OP_SW,   7, 2,  4),        /* SW   R7,4(R2)                         */
        ((uint32_t)OP_HALT << 26)
    };
    int n = (int)(sizeof prog / sizeof prog[0]);

    printf("GhanaCore-5 hazard demonstration -- forwarding + hazard detection ON\n");
    printf("Data memory word at byte address 40 pre-loaded with 100.\n\n");
    printf("Program:\n");
    for (int i = 0; i < n; i++) {
        Dec d = decode_instr(prog[i]);
        printf("  0x%04x  %08x  %s\n", i * 4, prog[i], d.mnem);
    }
    printf("\n");

    Machine m;
    mach_init(&m, 1, 1);
    memcpy(m.imem, prog, sizeof prog);
    m.nwords = n;
    m.dmem[40 / 4] = 100;
    m.trace  = stdout;

    mach_run(&m);
    mach_print_summary(&m, stdout, "demo (forwarding + HDU)");

    printf("\nFinal registers of interest:\n");
    printf("  R1 = %d   (40 + 7)\n",  m.reg[1]);
    printf("  R4 = %d   (R1 - 7)\n",  m.reg[4]);
    printf("  R6 = %d   (MEM[40])\n", m.reg[6]);
    printf("  R7 = %d   (R6 + R4)\n", m.reg[7]);
    printf("  MEM[44] = %d\n", m.dmem[44 / 4]);

    printf("\nExpected: exactly 1 load-use stall cycle, and 0 stalls for the\n"
           "SUB after the ADD -- that contrast is the whole point of Project 3.\n");

    printf("\n--- Control experiment: forwarding ON, hazard-detection unit OFF ---\n");
    Machine b;
    mach_init(&b, 1, 0);
    memcpy(b.imem, prog, sizeof prog);
    b.nwords = n;
    b.dmem[40 / 4] = 100;
    mach_run(&b);
    printf("R7 with HDU off = %d   (correct value is %d)\n", b.reg[7], m.reg[7]);
    printf("No crash, no warning -- just a wrong answer. This is the silent data\n"
           "corruption the report must show a specific test case for.\n");
    return 0;
}
