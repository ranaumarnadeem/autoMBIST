---
orphan: true
---

# Using the Safety Report in an FMEDA

`autombist safety-report` gives a safety engineer one input to a failure modes,
effects and diagnostic analysis (FMEDA): the fault coverage a BIST run measured,
per class of memory fault. The rest of the analysis, and every conclusion drawn
from it, stays with the engineer. For the command's options, see the
[CLI reference](cli-reference.md#safety-report).

## Who supplies what

| Input | Comes from |
|---|---|
| Coverage per fault class, in simulation | `safety-report` |
| What the figures depend on: algorithm, memory, fault list hash, tool versions | `safety-report` |
| The fault list, and whether it represents the memory | You |
| A failure rate for each fault class, from process and memory data | You |
| The mapping from the report's fault classes to the ones your analysis uses, including the standard's | You |
| Whether a power-on test can be credited for a given failure mode, and how the figure enters your safety metrics | You |

## Unweighted coverage and diagnostic coverage

A campaign counts fault records equally:

    unweighted coverage = faults detected / faults injected

Diagnostic coverage (DC) weights each class c_i by its failure rate lambda_i:

    DC = sum(lambda_i * c_i) / sum(lambda_i)

They are different numbers, and neither bounds the other. The unweighted figure
says more about which faults you chose to list than about the memory: listing ten
stuck-at faults for every coupling fault moves it, though the test is unchanged.
autoMBIST has no failure rates, so without `--failure-rates` the report gives only
the unweighted figure and says it is not DC.

Only the ratios of the rates matter, so any unit works. Within a class, records
count equally, and any `weight` on a record is ignored (the report says so when it
happens).

## Worked example

This is a real run on a 16-word, 4-bit memory. **The failure rates are
illustrative, made up for this page. Do not reuse them.** The fault list is short
so that it fits here, and a real one should be far larger. A test re-runs this
example and fails if the figures below stop matching.

`faults.txt`:

```text
SA0 1 0 0 0 0 0
SA1 2 1 0 0 0 0
SOF 3 2 0 0 0 0
TF0 4 0 0 0 0 0
TF1 5 1 0 0 0 0
AF_NOACC 6 0 0 0 0 0
AF_ALIAS 7 0 8 0 0 0
CFIN 9 0 10 0 0 0
CFIN 10 1 9 1 0 0
CFID 11 1 12 1 1 0
CFID 12 2 11 2 1 0
CFST 13 2 14 2 1 1
CFST 14 3 13 3 1 1
CFRD0 3 0 7 0 0 0
RDF0 1 3 0 0 0 0
RDF1 2 3 0 0 0 0
DRDF0 4 3 0 0 0 0
DRDF1 5 3 0 0 0 0
WDF0 6 3 0 0 0 0
IRF1 7 3 0 0 0 0
DYN_RDF01 8 3 0 0 0 0
DYN_DRDF10 9 3 0 0 0 0
DYN_IRF11 10 3 0 0 0 0
```

`rates.yml`:

```yaml
stuck_at: 30
stuck_open: 5
transition: 15
addressing: 10
coupling: 15
read_write: 15
dynamic: 5
retention: 5
```

The commands:

```bash
autombist test -aw 4 -dw 4 --algo march_ss --faults faults.txt --report march_ss.json --fmt json
autombist safety-report --campaign march_ss.json --failure-rates rates.yml --out march_ss
autombist test -aw 4 -dw 4 --algo mats_plus --faults faults.txt --report mats_plus.json --fmt json
autombist safety-report --campaign mats_plus.json --failure-rates rates.yml --out mats_plus
```

Detected out of injected, per class:

| Class | Illustrative rate | `march_ss` | `mats_plus` |
|---|---|---|---|
| stuck_at | 30 | 2/2 | 2/2 |
| stuck_open | 5 | 0/1 | 0/1 |
| transition | 15 | 2/2 | 2/2 |
| addressing | 10 | 2/2 | 2/2 |
| coupling | 15 | 7/7 | 6/7 |
| read_write | 15 | 6/6 | 3/6 |
| dynamic | 5 | 1/3 | 0/3 |
| retention | 5 | not measured | not measured |
| half_select | - | not measured | not measured |

The totals:

| Algorithm | Unweighted | DC, with these rates |
|---|---|---|
| `march_ss` | 86.95% | 86.66% |
| `mats_plus` | 65.21% | 75.35% |

What to take from it:

- **The weighting can go either way.** It lowers `march_ss` slightly and raises
  `mats_plus` by ten points, because these rates put weight on the classes
  `mats_plus` happens to catch. Other rates would give other figures.
- **An unmeasured class counts against you.** The list has no retention fault, so
  the rate of 5 given for retention counts as undetected, and neither algorithm
  can score above 95%. To measure it, add `DRF` records to the list. To leave it out,
  give it a rate of 0, and be ready to justify that in your analysis.
- **The list decides what you can see.** An earlier version of this list had every
  coupling aggressor above its victim, and `mats_plus` scored 4/4 on coupling.
  Adding the opposite directions exposed the miss shown above. Write the list to
  cover each class in the ways it can occur, and treat a perfect score on a few
  records with suspicion.

## Taking the figures into an FMEDA

1. Write a fault list for the classes your analysis needs, then run the campaign
   with the same algorithm and memory configuration as the design.
2. Run `safety-report` with your failure rates. Check its "Unmapped fault types"
   section: a user-defined fault type has no class and is left out of DC.
3. Map each fault class to the failure modes of your analysis, and take the
   per-class coverage from `safety_report.json`. Decide for each class whether the
   fault list is good enough to rely on.
4. Record the fault list hash and the tool versions next to the figures. The hash
   ignores the order of the list and the results, so two reports with one hash
   measured the same list.

If you run the campaign against a generated controller (`--fsm`), add
`--check-sequence`: `safety-report` refuses a campaign whose controller does not
implement its specified march sequence, or whose golden run was not clean.

## What autoMBIST does not provide

- **No ASIL or certification claim, and no tool qualification.** Nothing here
  qualifies autoMBIST as a tool for a safety-related development.
- **No Annex D mapping and no low/medium/high rating.** The fault classes come from
  the memory-test literature and were not checked against ISO 26262-5, and this
  page cites no table or clause of it for that reason.
- **Power-on BIST only.** There is no transparent or periodic in-field BIST, so
  nothing here is evidence for faults that appear while the chip is running.
- **No soft-error model, and no ECC.** Transient faults are not modelled.
- **No grading of the BIST controller.** Faults in the controller itself are not
  covered by this report.
- **No silicon data.** The figures come from injecting the listed faults into a
  behavioural memory model in simulation. They hold for that list, not for silicon.
