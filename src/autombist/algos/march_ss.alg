# March SS  (22n) — covers all static simple faults (adds WDF and DRDF over C-).
# Reference: Hamdioui, van de Goor & Rodgers, "March SS: A Test for All Static
# Simple RAM Faults," IEEE MTDT 2002.
# Reference coverage: 28/29 on faults.example.txt (SOF escapes solid backgrounds).
either w0
up   r0 r0 w0 r0 w1
up   r1 r1 w1 r1 w0
down r0 r0 w0 r0 w1
down r1 r1 w1 r1 w0
either r0
