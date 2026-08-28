/* ============================================================================
 * hazard_pipeline.c -- GhanaCore-5 golden simulator (Project 3, Hazard Watch)
 *
 * Written to the contract fixed by the Week 1 deliverables:
 *   - the simulator assembles .asm source itself
 *       hazard_sim <file.asm> --mode hazard [--no-hdu]
 *   - data memory defaults to MEM[word i] = (37*i + 11) % 1000
 *   - stdout carries the machine-readable line the Week 1 harness parses:
 *       cycles=N useful=N CPI=x.xxx stalls(lu/br)=N/N flushes=N
 *     followed by the register file as R0=.. R1=.. ...
 *   - forwarding follows the Week 1 truth table exactly, with no load guard;
 *     a load reaching the EX/MEM forwarding row is an ASSERTION (exit code 2),
 *     because the hazard-detection unit is supposed to make it unreachable.
 *
 * Build:  gcc -O2 -std=c11 -Wall -o hazard_sim sim/hazard_pipeline.c
 *
 * Options:
 *   --mode baseline|hazard   baseline = no forwarding, no hazard units
 *   --no-hdu                 disable the hazard-detection unit only
 *   --no-fwd                 disable the forwarding unit only
 *   --allow-mis-forward      downgrade the mis-forward assertion to a warning
 *                            so the silently-wrong result can be shown
 *   --hex FILE               load a machine-code / Logisim v2.0 raw image
 *                            instead of assembling source
 *   --data FILE              overlay "byte_address value" pairs on data memory
 *   --dmem-zero              start data memory at zero instead of the pattern
 *   --trace/--hazlog/--csv/--state/--summary FILE    evidence artefacts
 *   --quiet                  suppress the stdout summary
 * ==========================================================================*/
#include "pipeline_core.h"
#include "asm_core.h"

static void usage(void)
{
    printf("usage: hazard_sim <program.asm> [--mode baseline|hazard] [--no-hdu]\n"
           "       [--no-fwd] [--allow-mis-forward] [--hex FILE] [--data FILE]\n"
           "       [--dmem-zero] [--trace F] [--hazlog F] [--csv F] [--state F]\n"
           "       [--summary F] [--quiet]\n");
}

static int ends_with(const char *s, const char *suf)
{
    size_t a = strlen(s), b = strlen(suf);
    return a >= b && !strcmp(s + a - b, suf);
}

int main(int argc, char **argv)
{
    const char *prog = NULL, *hex = NULL, *mode = "hazard", *datafile = NULL;
    const char *tracef = NULL, *statef = NULL, *hazf = NULL, *sumf = NULL;
    const char *csvf = NULL, *unitf = NULL;
    int fwd = -1, hdu = -1, quiet = 0, dmem_zero = 0, allow_mf = 0;

    for (int i = 1; i < argc; i++) {
        if      (!strcmp(argv[i], "--hex")     && i + 1 < argc) hex      = argv[++i];
        else if (!strcmp(argv[i], "--asm")     && i + 1 < argc) prog     = argv[++i];
        else if (!strcmp(argv[i], "--mode")    && i + 1 < argc) mode     = argv[++i];
        else if (!strcmp(argv[i], "--data")    && i + 1 < argc) datafile = argv[++i];
        else if (!strcmp(argv[i], "--trace")   && i + 1 < argc) tracef   = argv[++i];
        else if (!strcmp(argv[i], "--state")   && i + 1 < argc) statef   = argv[++i];
        else if (!strcmp(argv[i], "--hazlog")  && i + 1 < argc) hazf     = argv[++i];
        else if (!strcmp(argv[i], "--summary") && i + 1 < argc) sumf     = argv[++i];
        else if (!strcmp(argv[i], "--csv")     && i + 1 < argc) csvf     = argv[++i];
        else if (!strcmp(argv[i], "--unit-csv") && i + 1 < argc) unitf  = argv[++i];
        else if (!strcmp(argv[i], "--fwd")     && i + 1 < argc) fwd      = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--hdu")     && i + 1 < argc) hdu      = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--no-hdu"))              hdu      = 0;
        else if (!strcmp(argv[i], "--no-fwd"))              fwd      = 0;
        else if (!strcmp(argv[i], "--allow-mis-forward"))   allow_mf = 1;
        else if (!strcmp(argv[i], "--dmem-zero"))           dmem_zero = 1;
        else if (!strcmp(argv[i], "--quiet"))               quiet    = 1;
        else if (argv[i][0] != '-' && !prog)                prog     = argv[i];
        else { usage(); return 1; }
    }
    if (!prog && !hex) { usage(); return 1; }

    int f = !strcmp(mode, "hazard") ? 1 : 0;
    int h = !strcmp(mode, "hazard") ? 1 : 0;
    if (fwd >= 0) f = fwd;
    if (hdu >= 0) h = hdu;

    Machine m;
    mach_init(&m, f, h);
    m.allow_misforward = allow_mf;
    if (!dmem_zero) mach_init_dmem_pattern(&m);

    int n;
    if (hex)                             n = mach_load_hex(&m, hex);
    else if (ends_with(prog, ".hex") ||
             ends_with(prog, ".rom"))    n = mach_load_hex(&m, prog);
    else                                 n = mach_load_asm(&m, prog);
    if (n <= 0) return 2;

    if (datafile) {
        FILE *df = fopen(datafile, "r");
        if (!df) { fprintf(stderr, "cannot open %s\n", datafile); return 2; }
        int addr, val;
        while (fscanf(df, "%d %d", &addr, &val) == 2)
            if (addr / 4 >= 0 && addr / 4 < DMEM_WORDS) m.dmem[addr / 4] = val;
        fclose(df);
    }

    if (tracef) { m.trace  = fopen(tracef, "w");  if (!m.trace)  { perror(tracef); return 2; } }
    if (hazf)   { m.hazlog = fopen(hazf,   "w");  if (!m.hazlog) { perror(hazf);   return 2; } }
    if (csvf)   { m.csv    = fopen(csvf,   "w");  if (!m.csv)    { perror(csvf);   return 2; } }
    if (unitf)  { m.unitcsv= fopen(unitf,  "w");  if (!m.unitcsv){ perror(unitf);  return 2; } }

    mach_run(&m);

    if (m.trace)  fclose(m.trace);
    if (m.hazlog) fclose(m.hazlog);
    if (m.csv)    fclose(m.csv);
    if (m.unitcsv) fclose(m.unitcsv);

    if (statef) { FILE *sf = fopen(statef, "w"); mach_dump_state(&m, sf);   fclose(sf); }
    if (sumf)   { FILE *sf = fopen(sumf,   "w"); mach_dump_summary(&m, sf, mode); fclose(sf); }

    if (!quiet) {
        /* the line the Week 1 test harness parses -- do not reformat */
        printf("cycles=%ld useful=%ld CPI=%.3f stalls(lu/br)=%ld/%ld flushes=%ld\n",
               m.cycles, m.useful,
               m.useful ? (double)m.cycles / m.useful : 0.0,
               m.stall_loaduse, m.stall_branch, m.flushes);
        for (int i = 0; i < NREG; i++) {
            printf("R%d=%d%s", i, m.reg[i], (i % 8 == 7) ? "\n" : "  ");
        }
        mach_print_summary(&m, stdout, mode);
    }
    return 0;
}
