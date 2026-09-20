# March C-  (10n) — the industry-default march.
# Detects SAF, TF, CFin, CFid, AF; misses WDF (no non-transition write),
# DRDF (no read-after-read), and (measured against faults.example.txt's
# mid-range placements) all twelve dynamic (2-operation) DYN_RDF/DYN_DRDF/
# DYN_IRF types. Reference coverage: 20/41 on faults.example.txt.
either w0
up   r0 w1
up   r1 w0
down r0 w1
down r1 w0
either r0
