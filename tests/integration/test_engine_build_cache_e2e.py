"""Verilator-gated integration tests for compile_engine's content-addressed
build cache (sweep 2026-08's "single biggest lever" performance finding:
run_algo_campaign compiled into a fresh TemporaryDirectory on every call,
even when an identical (mem, sources, top_module, sim) build had already run
moments earlier in the same process -- 93 of 113 measured campaign builds in
the integration suite were redundant).

Every test here uses its own tmp_path-scoped `cache_dir=` so none of them
ever touch the real shared cache (AUTOMBIST_ENGINE_CACHE / the OS temp dir
default) and none can pollute each other.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs Verilator 5.x on PATH (the fault engine cannot run under Icarus)",
)

import autombist.algo_engine as algo_engine_mod  # noqa: E402
from autombist.alg_spec import resolve_algo  # noqa: E402
from autombist.algo_engine import (  # noqa: E402
    FaultRecord,
    MemoryParams,
    compile_engine,
    find_engine_dir,
    run_algo_campaign,
    run_one,
    write_fault_list,
    _engine_build_cache_key,
    _resolve_engine_sources,
)
from autombist.fault_primitives import Effect, FaultPrimitive, Sensitize, default_registry  # noqa: E402
from autombist.fault_ram_gen import render_and_write  # noqa: E402


def _count_exec_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Wraps algo_engine._exec with a counting shim that still delegates to
    the real implementation, so the underlying verilator subprocess actually
    runs on every genuine build -- only cache HITS should avoid a new entry
    in the returned list."""
    real_exec = algo_engine_mod._exec
    calls: list[list[str]] = []

    def counting_exec(cmd, *, cwd, log_path=None):
        calls.append(cmd)
        return real_exec(cmd, cwd=cwd, log_path=log_path)

    monkeypatch.setattr(algo_engine_mod, "_exec", counting_exec)
    return calls


def test_engine_build_cache_key_is_sensitive_to_every_field_that_affects_the_build() -> None:
    """Verilator-gated only because _engine_build_cache_key calls the real
    `verilator --version` (a cache key computed before verilator's presence
    is confirmed would be meaningless -- compile_engine only ever calls this
    after _require_verilator already passed). Every argument that reaches
    compile_engine's verilator command line must change the key; sim/tool
    version are covered structurally (both functions of the one verilator on
    PATH here), not by parametrizing a second simulator/version."""
    engine_dir = find_engine_dir()
    mem = MemoryParams(addr_width=4, data_width=8)
    sources, top_module = _resolve_engine_sources(mem, engine_dir, Path("/unused"), None)

    base = _engine_build_cache_key(mem, sources, top_module, "verilator")
    assert base == _engine_build_cache_key(mem, sources, top_module, "verilator"), "identical inputs must match"

    assert base != _engine_build_cache_key(
        MemoryParams(addr_width=6, data_width=8), sources, top_module, "verilator"
    ), "addr_width must be part of the key"
    assert base != _engine_build_cache_key(
        MemoryParams(addr_width=4, data_width=16), sources, top_module, "verilator"
    ), "data_width must be part of the key"
    assert base != _engine_build_cache_key(
        MemoryParams(addr_width=4, data_width=8, words_per_row=2), sources, top_module, "verilator"
    ), "words_per_row must be part of the key"
    assert base != _engine_build_cache_key(
        mem, sources, "some_other_top", "verilator"
    ), "top_module must be part of the key"
    assert base != _engine_build_cache_key(
        mem, list(reversed(sources)), top_module, "verilator"
    ), "source content/order must be part of the key"


def test_second_compile_with_identical_inputs_skips_verilator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline claim: an identical (mem, sources, top_module, sim) build
    requested a second time must not invoke verilator again."""
    calls = _count_exec_calls(monkeypatch)
    mem = MemoryParams(addr_width=4, data_width=8)
    engine_dir = find_engine_dir()
    sources, top_module = _resolve_engine_sources(mem, engine_dir, tmp_path, None)
    cache_dir = tmp_path / "cache"

    artifact1 = compile_engine(
        mem, sources=sources, top_module=top_module, workdir=tmp_path / "run1", cache_dir=cache_dir
    )
    assert len(calls) == 1, "first call must actually build"
    assert artifact1.exe.exists()

    artifact2 = compile_engine(
        mem, sources=sources, top_module=top_module, workdir=tmp_path / "run2", cache_dir=cache_dir
    )
    assert len(calls) == 1, "second call with identical inputs must be a cache hit -- no new verilator invocation"
    assert artifact2.exe.exists()
    # Materialized into ITS OWN per-call workdir (run_one's cwd=artifact.workdir
    # contract is unchanged by caching), not literally the same path as the first.
    assert artifact2.exe != artifact1.exe
    assert artifact2.workdir == tmp_path / "run2"

    # Exactly one cache entry backs both -- not two, not zero.
    entries = [p for p in cache_dir.iterdir() if p.is_dir()]
    assert len(entries) == 1


def test_a_source_content_change_forces_a_real_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same claim: caching must never serve a stale
    build for genuinely different inputs. addr_width changes what -GAW= the
    verilator command line carries, so it must produce a SEPARATE cache
    entry and a SECOND real build, not a false hit."""
    calls = _count_exec_calls(monkeypatch)
    engine_dir = find_engine_dir()
    cache_dir = tmp_path / "cache"

    mem4 = MemoryParams(addr_width=4, data_width=8)
    sources4, top_module = _resolve_engine_sources(mem4, engine_dir, tmp_path, None)
    compile_engine(mem4, sources=sources4, top_module=top_module, workdir=tmp_path / "run4", cache_dir=cache_dir)
    assert len(calls) == 1

    mem6 = MemoryParams(addr_width=6, data_width=8)
    sources6, _ = _resolve_engine_sources(mem6, engine_dir, tmp_path, None)
    compile_engine(mem6, sources=sources6, top_module=top_module, workdir=tmp_path / "run6", cache_dir=cache_dir)
    assert len(calls) == 2, "a different addr_width must trigger a genuine second build, not a cache hit"

    entries = [p for p in cache_dir.iterdir() if p.is_dir()]
    assert len(entries) == 2


def test_two_custom_fault_type_registries_never_collide_in_the_cache(tmp_path: Path) -> None:
    """The correctness-critical case a build cache must get right: two
    DIFFERENT hand-authored fault_ram.sv contents (from two distinct
    add_fault_type registries), sharing everything else (mem, top_module,
    sim), sharing the SAME cache_dir, run in the same process. A cache keyed
    on anything less than the actual resolved source bytes (e.g. mem params
    alone) would serve registry B's compiled binary to registry A's
    campaign or vice versa -- silently wrong detection results with no
    error. Both custom types mirror IRF0's semantics exactly (see
    test_add_fault_type.py's proven-correct IRFP pattern), just under two
    different names/registries, so a correct result is unambiguous: each
    campaign must detect its OWN type, and the cache must end up with
    exactly two entries, not one collided entry."""
    registry_a = default_registry() + [
        FaultPrimitive(
            name="MYFA", category="read_effect",
            sensitize=Sensitize(pre="p0"), effect=Effect(kind="corrupt_read", value="p1"),
        )
    ]
    registry_b = default_registry() + [
        FaultPrimitive(
            name="MYFB", category="read_effect",
            sensitize=Sensitize(pre="p0"), effect=Effect(kind="corrupt_read", value="p1"),
        )
    ]
    mem = MemoryParams(addr_width=8, data_width=8, init_val=1)
    spec = resolve_algo("march_ss")
    cache_dir = tmp_path / "cache"

    fault_ram_a = render_and_write(registry_a, tmp_path / "fault_ram_a.sv")
    fault_ram_b = render_and_write(registry_b, tmp_path / "fault_ram_b.sv")
    assert fault_ram_a.read_text(encoding="utf-8") != fault_ram_b.read_text(encoding="utf-8")

    fault_a = FaultRecord("MYFA", vaddr=5, vbit=0, aaddr=0, abit=0, p0=0, p1=1)
    fault_b = FaultRecord("MYFB", vaddr=5, vbit=0, aaddr=0, abit=0, p0=0, p1=1)

    result_a = run_algo_campaign(
        mem, spec, [fault_a], fault_ram_sv=fault_ram_a, cache_dir=cache_dir, workdir=tmp_path / "run_a"
    )
    result_b = run_algo_campaign(
        mem, spec, [fault_b], fault_ram_sv=fault_ram_b, cache_dir=cache_dir, workdir=tmp_path / "run_b"
    )

    assert result_a.golden_clean is True
    assert result_a.detected == 1, "MYFA must be detected against registry A's own build"
    assert result_b.golden_clean is True
    assert result_b.detected == 1, "MYFB must be detected against registry B's own build"

    entries = [p for p in cache_dir.iterdir() if p.is_dir()]
    assert len(entries) == 2, f"expected two distinct cache entries for two distinct registries, got {entries}"


def test_engine_cache_disabled_via_env_var_always_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUTOMBIST_ENGINE_CACHE=off is the escape hatch back to pre-cache
    behavior -- every call must genuinely rebuild, and no cache_dir
    directory should even get created."""
    monkeypatch.setenv("AUTOMBIST_ENGINE_CACHE", "off")
    calls = _count_exec_calls(monkeypatch)
    mem = MemoryParams(addr_width=4, data_width=8)
    engine_dir = find_engine_dir()
    sources, top_module = _resolve_engine_sources(mem, engine_dir, tmp_path, None)
    cache_dir = tmp_path / "cache"

    compile_engine(mem, sources=sources, top_module=top_module, workdir=tmp_path / "run1", cache_dir=cache_dir)
    compile_engine(mem, sources=sources, top_module=top_module, workdir=tmp_path / "run2", cache_dir=cache_dir)

    assert len(calls) == 2, "AUTOMBIST_ENGINE_CACHE=off must force a real build every time"
    assert not cache_dir.exists()


def test_cache_hit_binary_still_runs_a_correct_campaign(tmp_path: Path) -> None:
    """Not just 'the file exists' -- the hardlinked/copied exe from a cache
    hit must actually execute correctly through run_one, matching the fresh
    build it was cloned from."""
    mem = MemoryParams(addr_width=4, data_width=8)
    engine_dir = find_engine_dir()
    sources, top_module = _resolve_engine_sources(mem, engine_dir, tmp_path, None)
    cache_dir = tmp_path / "cache"

    compile_engine(mem, sources=sources, top_module=top_module, workdir=tmp_path / "run1", cache_dir=cache_dir)
    artifact = compile_engine(
        mem, sources=sources, top_module=top_module, workdir=tmp_path / "run2", cache_dir=cache_dir
    )

    spec = resolve_algo("march_ss")
    fault = FaultRecord("SA0", vaddr=2, vbit=0, aaddr=0, abit=0, p0=0, p1=0)
    alg_file = spec.write_numeric(artifact.workdir / "spec.algc")
    fault_file = write_fault_list([fault], artifact.workdir / "faults.txt")
    golden_out = run_one(artifact, alg_file=alg_file, extra_plusargs=[f"+INIT={mem.init_val}"])
    assert "RESULT ESCAPED" in golden_out, golden_out
    faulted_out = run_one(
        artifact, alg_file=alg_file, fault_file=fault_file, index=0,
        extra_plusargs=[f"+INIT={mem.init_val}"],
    )
    assert "RESULT DETECTED" in faulted_out, faulted_out
