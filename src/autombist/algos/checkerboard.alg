# Checkerboard  (6n) — logical/address-LSB-parity checkerboard (NOT physical
# row/column adjacency: no consumer in this toolkit has physical geometry).
# Every cell holds the opposite value of its address neighbor at every
# snapshot; each cell is verified against both polarities. Same 4-element,
# march-X-shaped structure as march_x.alg (NOT March C-minus, despite
# superficially resembling its init/up/down/final-read shape).
# Detects SAF, TF, SOF, AF, RDF, IRF, and the dynamic CF variants (CFst/CFtr/
# CFir/CFrd/CFds); misses CFin/CFid (static coupling), CFwd, CFdrd, DRDF,
# WDF0 (catches WDF1 only), and (measured against faults.example.txt's
# mid-range placements) all twelve of the single-cell dynamic (2-operation)
# DYN_RDF/DYN_DRDF/DYN_IRF types. Reference coverage: 20/41 on
# faults.example.txt -- the same TOTAL as march_c, but a genuinely different
# profile: catches SOF (which march_c misses), misses CFin/CFid (which
# march_c catches).
# { updown(wc); up(rc,wcb); down(rcb,wc); updown(rc) }
either wc
up   rc wcb
down rcb wc
either rc
