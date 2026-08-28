# CPEN 438 — Project 3: Hazard Watch

Forwarding, Stalling and Branch Resolution in a COCOBOD Yield-Estimation Datapath

**Team:** _Group 5_
**Members:** 
Ishaan Bhardwaj - 11004272
Samia Soleimani - 11010910
Nyavor Cyril    - 11023595
Prince Philips  - 11218951
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

Generate the Logisim memory images from the seeded program:

```bash
python3 src/gen_hardware_images.py \
	--program datasets/cocobod_seed16993.hex \
	--outdir hardware/images
```

