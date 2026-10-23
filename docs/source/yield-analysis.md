---
orphan: true
---

# Repair Yield Analysis

How often does a spare budget repair a memory, how much yield does that buy,
and does the BIST, and the chip's own self-repair, actually get there? Three
commands answer it:

| Command | What it does | Speed |
|---|---|---|
| `yield-sim` | Draws random defects onto a fail bitmap and asks the exact repair solver whether the spares cover them. No RTL. | 100,000 trials in minutes |
| `yield-sweep` | The same question on the generated RTL: injects faults, simulates, and repairs from what the BIST observed, or runs the chip's own self-repair (`--self-repair`) | a few seconds per trial |
| `yield-multiplier` | Turns either sweep's repair curve into yield with redundancy and the yield multiplier, for an assumed defect-count distribution | instant |

Every number here comes from simulated defects and assumed defect statistics.
None of it is calibrated to silicon.

## Definitions

- **Repair rate R(k)**: the fraction of trials with k defects (or k faulty
  cells; the report says which in `defect_model.unit`) that end repaired. This
  is Jeong et al.'s raw repair rate (IEEE TVLSI 2009): it includes trials no
  repair could save. A trial whose observation did not complete (a scan that
  stopped, a word read back as X) is reported as invalid and left out, never
  counted as repaired. Each point carries a 95% Wilson interval.
- **Normalized repair rate**: repaired over exactly repairable (Jeong et al.),
  the measure of an analyzer rather than of the defects. `yield-sweep
  --self-repair` reports it for the on-chip analyzer against the exact solver.
- **Rate of overused spares (ROS)**: spares used over the minimum, minus one
  (Jeong et al.; the paper gives the formula but not whether it is averaged per
  trial or taken as a ratio of sums; autoMBIST takes the ratio of sums over a
  point's repaired trials). `yield-sim --min-spares` reports it.
- **Spare utilization**: mean spares used over the spare budget.

## Yield with redundancy and the multiplier

A die with k defects is repairable with probability R(k), so

    Y_red = sum over k of P(k) R(k),   with R(0) = 1
    Y0    = P(0)                       the yield with no redundancy
    M     = Y_red / Y0                 the yield multiplier

With Poisson defect counts this is Ramadan's general model (Intel Technology
Journal, Q4 1997, eq. 26, M = 1 + R1 lam + R2 lam^2/2! + ...); with negative
binomial counts of clustering alpha it is Stapper's (IBM J. Res. Dev. 1980,
1989). Every sweep point has k of at least 1, so M exists only once a
distribution is assumed: an initial yield Y0 (or a mean defect count lam) and
alpha. `yield-multiplier` therefore reports one row per assumed (alpha, Y0)
pair, never one number:

```bash
autombist yield-multiplier --report out/yield_sim.json --y0 0.9,0.5,0.2 --alpha poisson,2,0.5
```

- The curve must cover every k from 1 to its largest, since the weighting
  needs each term (`--faults 1..K`).
- Beyond the largest k the repair rate is taken as 0, a lower bound; the upper
  figure adds the remaining probability at the smallest measured rate (the
  rate never rises with k).
- The band combines the per-point Wilson intervals with a Bonferroni
  correction, so it holds jointly at the stated confidence.
- When defects can land in the spares (`--expose-spares`), the mean defect
  count scales with the physical area over the base area; the report records
  that `area_ratio` and `yield-multiplier` applies it.
- `--alpha itc2020` and `--lam itc2020` are the ITC'2020 RA Benchmarks'
  defect-count parameters (alpha 0.6212, mean 2.006 per bank, about 41%
  initial yield).

The weighting is tested against Stapper's 1989 Table 4 (10 blocks at 10%
yield, 0 to 5 spare blocks, alpha 0.5, 1, 2 and Poisson): from an exact
occupancy repair curve it reproduces all 24 entries within 0.1 point, 22 of
them to the printed digit.

## Defect models

`yield-sim` and `yield-sweep --defects` draw from these presets. Coordinates
are (row, bit), one word per row, laid out as OpenRAM's port 0: the row decoder
on the left, the sense amplifiers below, spare rows above the array and spare
columns to its right.

| Preset | What a defect is |
|---|---|
| `singles` | one cell |
| `ohler-d1`, `ohler-d2`, `ohler-d3` | Ohler, Hellebrand and Wunderlich (DDECS 2007, Table 1): full rows and columns, short lines, clusters and single cells in the paper's mixes |
| `itc2020-1020`, `-1050`, `-3020`, `-3050` | the ITC'2020 RA Benchmarks' sequential placement (Lee et al., ITC 2020): each next faulty cell lands next to, or in line with, the previous one, with p_line and p_cluster from the last four digits |

The papers do not give every shape parameter, so these are autoMBIST's own,
fixed before any calibration run: a line is 3 to 8 cells, a cluster's height
and width are each 1 to 3 (a 1x1 draw is redrawn) and every cell in it fails. A
word-line open fails its row from the open to the far end and a bit-line open
its column to the top, so with spares exposed they run on into the spare
columns and spare rows. Fig. 5 of the ITC'2020 paper leaves two points open,
whether p_line applies to each orientation or to both together and whether k
counts faults per bank or over the whole memory, so each combination is a named
reading (`--itc-reading`).

## Calibration

The protocol was committed before the first run (`scripts/calibrate_yield.py`),
and its results are in `tests/data/yield_calibration.json`.

**The gate: ITC'2020 MLS3050.** Two 512 x 256 banks, 3 spare rows and 3 spare
columns per bank, p_line 30%, p_cluster 50%; the paper's branch-and-bound repair
rate at 8 to 12 faults per bank (Table II), 100,000 trials per point on both
sides. Every reading is run; the one with the smallest sum of z^2 is chosen,
labelled inferred, and passes when every point is within |z| of 3.

| Faults per bank | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|
| Paper (B&B) | .9761 | .8894 | .7249 | .4976 | .2810 |
| per orientation, per bank (chosen) | .9726 | .8965 | .7389 | .5103 | .2963 |
| z | -5.0 | 5.2 | 7.1 | 5.7 | 7.5 |
| per orientation, whole memory | .6487 | .4778 | .3173 | .1803 | .0897 |
| both together, per bank | .6590 | .3698 | .1563 | .0489 | .0129 |
| both together, whole memory | .3106 | .1508 | .0572 | .0169 | .0049 |

**The gate failed.** The chosen reading is within 0.35 to 1.5 points of the
paper at every count, but with 100,000 trials on both sides that is well
outside sampling error, so some part of the paper's generator is still not
reproduced. Nothing was tuned after the run. The paper's text says the faults of
both banks are generated together over the whole memory; only per-bank counts
come close to its numbers.

**A comparison: Ohler DDECS 2007, mix d2.** 1024 x 1024, 5 spare rows and 5
spare columns, 1 to 15 defects; the paper's 1000 trials, 10,000 here. Not a
gate: the paper gives no line lengths or cluster shapes ("a cluster fault
affecting up to 33 cells").

| Defects | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|
| Paper | 1.000 | .993 | 1.000 | .970 | .873 | .835 | .753 | .753 | .047 | .000 |
| default shapes | 1.000 | 1.000 | 1.000 | .999 | .993 | .964 | .846 | .597 | .035 | .001 |
| clusters up to 6 x 6 | .984 | .973 | .945 | .909 | .853 | .761 | .638 | .472 | .028 | .001 |
| lines of 2 to 16 | 1.000 | 1.000 | 1.000 | .999 | .994 | .965 | .847 | .596 | .036 | .001 |

The default shapes pass the declared structural checks (at least .99 up to 3
defects, at most .01 from 12, the largest drop at 11 = R + C + 1) but repair more
often than the paper between 6 and 9 defects; larger clusters come closer there
and fail the low-k check. The paper's identical .753 at 9 and 10 defects may be a
typo.

## The repair solver

`repair.bira.analyze()` (the solver the RTL repair flow uses) is unchanged.
`analyze_detailed()` adds:

- **a minimum-spares objective**: must-repair lines, then iterative deepening
  on the total from Konig's matching bound;
- **faulty spares**: a spare row in use serves every bit of the row it
  replaces, so its own faulty bits must be column-repaired, and likewise for a
  spare column; a faulty corner forbids its spare row and spare column together.
  Each usable set of faulty spares reduces to the classic problem with those
  lines repaired in advance, so a clean column may be repaired only to unlock a
  faulty spare row;
- solver statistics, and `verify_solution()`, which checks a repair cell by cell.

Both are tested against a brute-force oracle that tries every spare assignment
through the physical address mapping. With defects in the spares, the rule
Amirkhanyan et al. found for resistive opens (EWDTS 2024: a word-line open needs
a spare row, a bit-line open a spare column) comes out of the spare faults with
no special case.

## Fail-bitmap shapes

`yield-sim --classes` sorts each fail bitmap into the classes of Lin et al.
(IEEE TC 2016): single, twin (row, column, diagonal), segment, line, cluster
(at least 75% of its bounding box), cross and other. Lines are extracted first;
the rest is grouped by Chebyshev single linkage. Each pattern also carries its
spare cost, a must-repair flag and an orthogonal-single flag. Every defect's
own shape also gives the class it should get, so the report's confusion counts
measure the classifier: with one defect at a time it is exactly diagonal, and
the merge rate (defects whose pattern also holds another) and the defect
spectrum (patterns found minus defects injected) show how classes blur as k
grows. Classifying bitmap shapes is published work (Lin et al.; Bernardi et al.,
IEEE TETC 2026, with a CNN); this does not replace the fault-type grouping of
the diagnosis reports.

## On the RTL: observed versus truth

`yield-sweep` generates and compiles the wrapper once and rewrites the
saboteur's fault masks for each trial. It observes the failures one of two ways:

- `--observe functional` (the default) reads every word back through the
  functional port, solid 0s then solid 1s. It sees stuck-at faults only.
- `--observe march` logs every read the algorithm's own compare flags during
  the march. It sees transition faults too, when the algorithm reads soon enough:
  `march-raw` sees the saboteur's delayed writes, `march-c` does not.

A trial counts as repaired only when the repair computed from the observed
cells also covers every injected cell. Each point also gives the repair rate
from the injected cells alone, the trials whose observed repair missed an
injected cell (escapes), and the observed cells never injected (overkill,
expected 0). On the RTL the march log matches the injected stuck-at cells for
all seven algorithms.

## On-chip self-repair

`yield-sweep --self-repair` builds the wrapper with on-chip self-repair over the
spare budget (the 2D analyzer when there are spare columns, greedy or `--analyzer deferred`) on a
fault-injectable memory model: stuck-at masks over the whole physical array,
spare rows and spare lanes included, loaded when the simulation starts. Each
trial logs what the analyze pass saw, runs the self-repair, decodes the latched
repair signature, and scans the repaired memory independently. A trial is
repaired when the chip reports a pass and the scan reads back clean.

The exact solver, which knows the spare faults, gives the upper bound. Each
point reports the normalized repair rate and, for every trial the exact solver
repairs and the chip does not, why:

| Cause | Meaning |
|---|---|
| `analyzer` | the on-chip analyzer's repair leaves a cell it observed uncovered, on a fail map the exact solver repairs (`--analyzer deferred` closes most of these) |
| `faulty_spare` | the chip's repair covers every faulty cell but allocates a faulty spare; the verify pass catches it, and the analyzer does not reallocate (unless the wrapper is built with `onchip_spare_retry`) |
| `observation_escape` | the analyze pass did not observe every faulty cell |
| `false_pass` | the chip reported a pass but the repaired memory still reads wrong |

The analyze pass runs with the remap clear, so the analyzer never sees a spare's
own defects; only the verify pass does. With `--diagnosis-entries N` the
diagnosis log is built too and its overflows are counted.

An example, small enough to read rather than to quote (20 trials per point, so
each rate is uncertain by several points): a 16 x 8 memory with 2 spare rows and
1 spare column, Ohler's mix d2, defects allowed in the spares.

```bash
autombist yield-sweep --config config.yml --faults 1..5 --trials 20 --spare-rows 2 --spare-cols 1 \
    --self-repair --expose-spares --defects ohler-d2
```

| Defects | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Repaired on chip | .90 | .75 | .55 | .05 | .00 |
| Repairable (exact solver, spare faults known) | .90 | .90 | .85 | .25 | .05 |
| Normalized repair rate | 1.00 | .83 | .65 | .20 | .00 |
| Not repaired because of: the analyzer | 0 | 1 | 1 | 2 | 1 |
| Not repaired because of: a faulty allocated spare | 0 | 2 | 5 | 2 | 0 |

Every gap is accounted for. The two single defects nothing repairs are 3 x 2
clusters, which need two spare columns or three spare rows. Faulty spares (9
trials) cost the chip more than its greedy analyzer does (5) here: the analyzer
allocates a spare it has never tested, and once the verify pass fails it does
not try another. (The greedy analyzer up to 0.4.0, which gave up at its first
row request with no row left, repaired .45 at 3 defects on the same seeds.)

`redundancy.onchip_spare_retry` (see the configuration page) makes it try
another: a spare the verify pass catches is retired and the analysis re-run on
the rest. `redundancy.onchip_analyzer: deferred` replaces the greedy single
pass with two passes that defer lone first sightings. The same sweep, the same
seeds, with `--spare-retry`, then with `--spare-retry --analyzer deferred`:

| Defects | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Repaired on chip, greedy | .90 | .75 | .55 | .05 | .00 |
| Repaired on chip, greedy with retry | .90 | .80 | .70 | .05 | .00 |
| Repaired on chip, deferred with retry | .90 | .85 | .75 | .05 | .05 |
| Normalized repair rate, deferred with retry | 1.00 | .94 | .88 | .20 | 1.00 |
| Not repaired because of the analyzer: greedy with retry | 0 | 2 | 3 | 4 | 1 |
| Not repaired because of the analyzer: deferred with retry | 0 | 1 | 2 | 4 | 0 |

With retry no trial fails on a faulty spare any more; some of the trials it
saves then fall to the analyzer instead, because retiring a spare leaves fewer
for it to work with. The deferred analyzer recovers about half of those. At 4
defects only 5 of the 20 trials are repairable at all, and none of the
analyzers repairs more than one of them: with defects in the spares and the
spares retired, the remaining budget leaves no slack for a heuristic.

## Report files

- `yield_sweep.json` stays at schema 1.x, with additive keys only (1.1): each
  trial's `status` and `invalid_reason`; each point's `valid_trials`,
  `repaired_count`, `invalid_count`, `wilson` and, where they apply,
  `observed_vs_truth` and `self_repair`; the top-level `engine`, `seeding`,
  `observation` and `defect_model` blocks. A 1.0 reader still parses it. The
  repair rate now counts valid trials only.
- `yield_sim.json` has the same shape, with `geometry`, `spare_budget` and the
  solver's `analyzer` block.
- `yield_multiplier.json` (schema 1.0) records the curve it read, the source
  report's SHA-256, every assumed distribution's figures and bands, and the
  disclaimer.

## Limits

- Banks are independent: no spares shared between banks (the ITC'2020 global and
  common spares).
- One word per row (`words_per_row` 1) and logical coordinates; column-mux
  interleaving and address scrambling are not modelled.
- One fault type per RTL sweep; trials run one after another.
- Unknown bitmap shapes are classed `other`, not named by run-length strings.
