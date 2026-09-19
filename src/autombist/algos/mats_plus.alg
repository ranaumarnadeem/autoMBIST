# MATS+  (5n) — minimal march. Detects SAF, AF and some read/transition faults.
# Reference coverage: 13/41 on faults.example.txt (unchanged numerator --
# measured against faults.example.txt's mid-range placements, detects none
# of the twelve dynamic (2-operation) types added since).
either w0
up   r0 w1
down r1 w0
