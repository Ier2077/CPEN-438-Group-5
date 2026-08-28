; Project-2 baseline: the same routine, NOP-padded by the assembler
; (no forwarding hardware, no hazard-detection unit)
; seed=16993  status=provisional  drawn_on=19 August 2026
; 24 NOPs inserted into 19 instructions

    ADDI R17, R0, 20
    NOP
    NOP
    LW   R15, 0(R17)
    LW   R22, 4(R17)
    NOP
    NOP
    ADD  R8, R15, R22
    LW   R21, 8(R17)
    NOP
    ADDI R9, R8, 30
    ADDI R12, R0, 833
    NOP
    ADD  R19, R9, R21
    NOP
    NOP
    ADDI R6, R19, -1
    NOP
    NOP
    SUB  R11, R6, R9
    NOP
    NOP
    AND  R23, R11, R6
    NOP
    NOP
    ADDI R13, R23, 0
    NOP
    NOP
    SLT  R10, R13, R12
    NOP
    NOP
    BEQ  R10, R0, STORE
    ADDI R13, R12, 0
    NOP
    NOP
STORE:
    SW   R13, 12(R17)
    ADDI R8, R13, 16
    NOP
    NOP
    ADD  R9, R8, R13
    NOP
    NOP
    SW   R9, 16(R17)
