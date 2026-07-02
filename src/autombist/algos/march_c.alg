# March C-  (10n) — the industry-default march.
# Detects SAF, TF, CFin, CFid, AF; misses WDF (no non-transition write) and
# DRDF (no read-after-read). Reference coverage: 14/19 on faults.example.txt.
either w0
up   r0 w1
up   r1 w0
down r0 w1
down r1 w0
either r0
