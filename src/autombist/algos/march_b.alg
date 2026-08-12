# March B  (17n) — detects LINKED coupling faults, not just the pairwise model.
# Reference: Suk & Reddy, "A March Test for Functional Faults in Semiconductor
# Random Access Memories," IEEE Trans. Computers C-30(12), 1981.
#
# The distinguishing citable claim (a literature claim, NOT independently
# re-derived here): March B detects linked CFin/CFid coupling faults — where one
# coupling fault masks another — whereas the pairwise coupling model this
# project's CFIN/CFID primitives implement covers only unlinked pairs. Also one
# of the march tests named in high-integrity/functional-safety embedded
# literature (IEC 61508-7, the parent standard ISO 26262 adapts) alongside
# MATS++, March C-, and March RAW.
#
# Reference coverage: 19/29 on faults.example.txt (see engine/README.md's table)
# — one better than March C-'s 20/29, and the extra catch is SOF. That is a
# measured result with a mechanical explanation: element 2 reads the same cell
# as r0 ... r1 ... r0 within one address visit, so the output keeper an SOF
# models is forced to hold across reads of OPPOSITE expected data, which is
# exactly the condition engine/README.md documents as necessary to expose it.
# March C- never reads a cell twice at opposite polarity inside one visit.
#
# The linked-fault claim above is NOT what those 19/29 measure:
# faults.example.txt contains only unlinked faults, so the coupling
# sophistication March B is actually chosen for has nothing here to exercise.
either w0
up   r0 w1 r1 w0 r0 w1
up   r1 w0 w1
down r1 w0 w1 w0
down r0 w1 w0
