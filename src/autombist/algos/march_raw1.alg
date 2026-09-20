# March RAW1 ("read-after-write")  (13n) -- diagnostic reference for the
# single-cell dynamic (2-operation) fault family.
# Reference: Hamdioui, Al-Ars & van de Goor, "Testing Static and Dynamic
# Faults in Random Access Memories," VTS 2002, Figure 3 (independently read
# from the primary source, not transcribed from a secondary summary).
#
# Detects all 12 single-cell dynamic FPs (DYN_RDF/DRDF/IRF x 00/01/10/11):
# each dRDF/dIRF FP is both sensitized AND detected by its own qualifying
# march element (the write immediately followed by the read IS the fault
# primitive's own sensitizing sequence); each dDRDF FP is sensitized by that
# element but only DETECTED by the NEXT element's read (the deceptive first
# read reports the correct value on purpose -- only the following element's
# read sees the corrupted storage). This is the paper's own Table 5, not a
# claim independently re-derived here.
#
# NOT a claim of static coverage -- this test exists to diagnose the dynamic
# family specifically (see the paper's own Section 6.1: "If the purpose is
# to diagnose the FFMs, then March RAW1 has to be used"), not to compete
# with March SS/AB on general coverage. Reference coverage: 36/41 on
# faults.example.txt overall (real run_algo_campaign result), 12/12 on the
# dynamic types specifically -- the 36 is incidental to its purpose, not
# tuned for. autoMBIST's synthesizer already finds an efficient covering
# test tailored to the live registry (see `synth` in the research shell);
# this is a literature-matched reference algorithm, the same role
# march_b.alg plays for its own citable claim.
either w0
either w0 r0
either r0
either w1 r1
either r1
either w1 r1
either r1
either w0 r0
either r0
