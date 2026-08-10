# March Y  (8n) -- shorter than March C- (10n); adds DRDF0/1 and SOF, trades
# away CFID. Catalogued alongside March X/March C/March C-/March A/March B in
# the standard march-test survey tables (e.g. A.J. van de Goor, "Using March
# Tests to Test SRAMs," IEEE Design & Test of Computers 10(1), 1993;
# attributed there to Nair, Thatte & Abraham, "Efficient Algorithms for
# Testing Semiconductor Random-Access Memories," IEEE Trans. Computers
# C-27(6), 1978 -- attribution not independently re-verified against primary
# source, same caveat march_b.alg's header already carries for its own cite).
# Canonical form: { updown(w0); up(r0,w1,r1); down(r1,w0,r0); updown(r0) }
#
# Measured on faults.example.txt, addr_width=8 data_width=8 init=1 (this
# project's standard measurement config, matching engine/README.md's table):
# 16/19, vs March C-'s 14/19 -- net +2. Per-fault delta vs March C-: GAINS
# DRDF0, DRDF1, SOF; LOSES CFID.
#
# Mechanism (re-derived directly from fault_ram.sv's DRDF/SOF/CFID logic):
# - DRDF0/DRDF1: element 2's ...w1,r1 and element 3's r1,w0... each read a
#   polarity with no further write to that address before the SAME polarity
#   is read again elsewhere in the sequence (e.g. element 2's r1 arms DRDF1
#   silently; element 3's r1 at the same address, with no intervening write
#   to it, exposes the flip). March C- never re-reads one polarity at one
#   address without an opposite-polarity write in between, so it can't.
# - SOF: element 2's r0,w1,r1 means the address immediately before any given
#   one (same ascending pass) just left the shared dout output-keeper on the
#   OPPOSITE polarity from what this address's own first read expects -- so
#   a SOF-affected address mismatches on its very first read. March C-'s
#   elements read only one polarity per full address pass, so neighbor dout
#   state never disagrees with what's expected.
# - CFID (lost): the aggressor's only up-transition write here is in element
#   2 (ascending); for a victim address BELOW the aggressor's address, the
#   victim's own ops for that pass already ran before the aggressor fires --
#   nothing re-checks it before a legitimate rewrite erases the forced
#   value. March C- also does an up-transition write in a DESCENDING element
#   (down r0,w1), where the (higher-address) aggressor is processed before
#   the victim in the same pass, catching the fresh force immediately.
either w0
up   r0 w1 r1
down r1 w0 r0
either r0
