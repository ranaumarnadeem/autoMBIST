# March SS  (22n) — covers all static simple faults (adds WDF and DRDF over C-).
# Reference: Hamdioui, van de Goor & Rodgers, "March SS: A Test for All Static
# Simple RAM Faults," IEEE MTDT 2002.
# Reference coverage: 32/41 on faults.example.txt (SOF escapes solid
# backgrounds). Covers all static simple faults in this list, plus four of
# the twelve dynamic (2-operation) types added since: DYN_RDF00/11 and
# DYN_IRF00/11 (measured, real run_algo_campaign result -- the
# opposite-polarity `*01`/`*10` half is never sensitized here at all, no
# same-address transition-write-then-read adjacency exists in elements 2-5
# below; DYN_DRDF00/11 ARE sensitized by the same non-transition `w0 r0`/
# `w1 r1` pair that catches DYN_RDF00/11, but escape anyway -- DRDF's
# sensitizing read is deceptive by definition and needs a distinct later
# read to expose it, which this structure doesn't happen to provide for
# those two; see engine/README.md's "Dynamic (2-operation) faults").
either w0
up   r0 r0 w0 r0 w1
up   r1 r1 w1 r1 w0
down r0 r0 w0 r0 w1
down r1 r1 w1 r1 w0
either r0
