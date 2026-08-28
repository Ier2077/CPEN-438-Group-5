# forwarding-aware scheduled version of cocobod_seed16993.asm
# reordering preserves RAW / WAR / WAW and memory order

    ADDI R17, R0, 20
    LW   R15, 0(R17)
    LW   R22, 4(R17)
    LW   R21, 8(R17)
    ADD  R8, R15, R22
    ADDI R9, R8, 30
    ADDI R12, R0, 833
    ADD  R19, R9, R21
    ADDI R6, R19, -1
    SUB  R11, R6, R9
    AND  R23, R11, R6
    ADDI R13, R23, 0
    SLT  R10, R13, R12
    BEQ  R10, R0, STORE
    ADDI R13, R12, 0
STORE:
    SW   R13, 12(R17)
    ADDI R8, R13, 16
    ADD  R9, R8, R13
    SW   R9, 16(R17)
