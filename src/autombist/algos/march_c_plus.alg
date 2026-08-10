# March C+  (14n) -- the un-reduced original March C algorithm.
# Reference: D. S. Suk & S. M. Reddy attribute the base sequence to
# Marinescu (1982); the canonical statement used here follows A. J. van de
# Goor, "Testing Semiconductor Memories: Theory and Practice," Wiley, 1991
# (March C, section on march tests) -- {(w0)} {up(r0,w1,r1)} {up(r1,w0,r0)}
# {down(r0,w1,r1)} {down(r1,w0,r0)} {(r0)}.
#
# March C- (this project's `march_c.alg`, 10n) is the REDUCED form of this
# same test: van de Goor derives March C- by dropping the trailing verify
# read of each of the four middle elements (the r1 after w1, the r0 after
# w0). This file puts that read back, so it is March C- plus exactly one
# extra op per middle element -- +4n over march_c's 10n, hence "C+" here.
#
# Measured on faults.example.txt (19 entries, addr_width=8 data_width=8
# init=1, real Verilator run via run_algo_campaign): 17/19, vs march_c's
# 14/19 -- +3, zero regressions (the 3 newly caught are DRDF0, DRDF1, SOF;
# WDF0/WDF1 still escape both, as expected -- this family never performs a
# non-transition write). Mechanism, confirmed by inspecting elem/op
# attribution rather than assumed: the appended read makes the LAST read of
# one element and the FIRST read of the next element land on the SAME
# address with NO intervening write whenever a middle element's traversal
# direction matches its successor's start point (e.g. element 2 ends
# "...w0 r0" at the top address, element 3 begins "down r0..." at that same
# top address) -- exactly the back-to-back same-polarity double-read March
# C- structurally lacks, which is what DRDF (destructive-read) detection
# needs, and the polarity churn it creates on the shared per-bit output
# latch is what exposes SOF here.
#
# Measured on 5 independent N=300 random-fault samples (same MemoryParams,
# generate_random_faults seeds 0/1/2/3/42): march_c+ beats march_c by
# 39/47/49/37/47 respectively, zero regressions in any sample (index-aligned
# fault-by-fault comparison, not just the totals). This is the likely source
# of a "catches 46 more" figure measured elsewhere at N=300 scale -- not
# reproducible on the 19-entry faults.example.txt, where march_c already
# scores 14/19 and 5 is therefore the hard ceiling on any possible gain.
either w0
up   r0 w1 r1
up   r1 w0 r0
down r0 w1 r1
down r1 w0 r0
either r0
