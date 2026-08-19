# CPEN 438 — Project 3: Hazard Watch

Forwarding, Stalling and Branch Resolution in a COCOBOD Yield-Estimation Datapath

**Team:** _[fill]_
**Members:** _[fill]_
**Seed:** 16993 — provisional, see the seed field of the project proposal

## Repository layout

| Path | Contents |
|---|---|
| `docs/` | Design document, proposal, weekly reports |
| `src/` | Program generator and assembler |
| `student_implementation/` | Golden cycle-accurate simulator |
| `tests/` | Hand-derived hazard test vectors |
| `datasets/` | The team's seeded COCOBOD routine |
| `results/` | Simulation output, traces, hazard classification |
| `presentation/` | Paper-review deck, final defence deck |
| `report/` | IEEE-style technical report |
| `ai_use_declaration/` | AI-use declaration |

## Build and run

```bash
gcc -O2 -Wall -Wextra -o hazard_sim student_implementation/hazard_pipeline_sim.c
python3 tests/hazard_test_vectors.py --sim ./hazard_sim
./hazard_sim datasets/cocobod_seed16993.asm --compare
```

> M4: expand this section so any result in the report can be reproduced from
> this file alone by someone who has never seen the project. That is the
> rubric wording, and it is your defence question.
