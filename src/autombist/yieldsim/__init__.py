"""Pure repair-yield simulation: sweep core, statistics, yield models, defect
generation and fail-bitmap shape classification.

This package is extractable on its own. It imports only the standard library and
``autombist.repair`` (the exact repair solver), never the generator, the
simulator runner or the CLI; ``tests/software/test_repair_package_boundary.py``
enforces that. The RTL-backed sweep lives outside it, in
``autombist.yield_sweep``, and writes the same report shape; any trial engine
plugs into :func:`yieldsim.sweep.run_sweep` through the
:class:`yieldsim.sweep.TrialEngine` protocol.
"""
