; COCOBOD regional cocoa-yield estimation routine
; CPEN 438 Project 3 -- Hazard Watch
; team=TEAM-UNSET  seed=16993  instructions=19
; yield = f(rainfall, fertiliser, tree_age), clamped to a ceiling
;
; register map: BASE=R17, RAIN=R15, FERT=R22, AGE=R21, T1=R8, T2=R9, T3=R19, T4=R6, T5=R11, T6=R23, YIELD=R13, THRESH=R12, FLAG=R10

    ADDI R17, R0, 20            ; [00] R17 = base address of this region's record
    LW   R15, 0(R17)            ; [01] rainfall_mm      <- MEM[base+0]
    LW   R22, 4(R17)            ; [02] fertiliser_kg    <- MEM[base+4]
    ADD  R8, R15, R22           ; [03] LOAD-USE: consumes FERT one cycle after its LW
    LW   R21, 8(R17)            ; [04] tree_age_years   <- MEM[base+8]
    ADDI R9, R8, 30             ; [05] T2 = T1 + seeded weight   (RAW dist-1 on T1 -> EX/MEM forward)
    ADDI R12, R0, 833           ; [06] THRESH = seeded yield ceiling
    ADD  R19, R9, R21           ; [07] T3 = T2 + tree_age        (RAW dist-1 and dist-N)
    ADDI R6, R19, -1            ; [08] T4 = T3 - age penalty     (RAW dist-1 chain)
    SUB  R11, R6, R9            ; [09] T5 = T4 - T2              (RAW dist-1 + dist-2)
    AND  R23, R11, R6           ; [10] T6 = T5 & T4              (RAW dist-1 + dist-2)
    ADDI R13, R23, 0            ; [11] YIELD = T6                (RAW dist-1)
    SLT  R10, R13, R12          ; [12] FLAG = (YIELD < ceiling)? (RAW dist-1 on YIELD)
    BEQ  R10, R0, STORE         ; [13] BRANCH: if FLAG==0 skip the clamp  (resolved in ID)
    ADDI R13, R12, 0            ; [14] clamp YIELD to the ceiling (branch-not-taken path)
STORE:
    SW   R13, 12(R17)           ; [15] STORE: MEM[base+12] <- estimated yield
    ADDI R8, R13, 16            ; [16] running regional accumulator
    ADD  R9, R8, R13            ; [17] T2 = T1 + YIELD           (RAW dist-1 + dist-2)
    SW   R9, 16(R17)            ; [18] MEM[base+16] <- accumulator (SW reads rt as a SOURCE)
