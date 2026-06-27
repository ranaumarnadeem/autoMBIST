import os
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer, with_timeout

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.fault_gen import generate_fault_files
from autombist.fault_gen import FaultType


def _safe_int(handle) -> int | None:
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


def _get_hier_value(root, dotted_path: str) -> int:
    current = root
    for name in dotted_path.split("."):
        current = getattr(current, name)
    return int(current.value)


def _read_words(path: Path) -> list[int]:
    return [int(line, 16) for line in path.read_text(encoding="ascii").splitlines() if line.strip()]


def _format_ascii_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def _format_row(values: list[str]) -> str:
        cells = [f" {value:<{widths[index]}} " for index, value in enumerate(values)]
        return "|" + "|".join(cells) + "|"

    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    rendered = [border, _format_row(headers), border]
    for row in rows:
        rendered.append(_format_row(row))
    rendered.append(border)
    return "\n".join(rendered)


def _bitcount(value: int) -> int:
    return int(value).bit_count()


def _format_addr_bits_map(mapping: dict[int, int], top_n: int = 8) -> str:
    if not mapping:
        return "-"
    ranked = sorted(mapping.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    return ", ".join(f"0x{addr:04X}:{count}" for addr, count in ranked)


def _module_outdir(memory_name: str) -> Path:
    """Resolve the generated module directory, honoring --out/OUTDIR via MODULE_OUTDIR.

    Falls back to REPO_ROOT/out/<memory> only when the env var is absent (e.g. a
    bare `make` run with no autombist OUTDIR). This is what lets a custom --out
    directory (or a pip-installed run) find the right fault masks.
    """
    env = os.getenv("MODULE_OUTDIR", "").strip()
    return Path(env) if env else REPO_ROOT / "out" / memory_name


def _fault_mask_paths(memory_name: str, fault_type: str) -> tuple[Path, Path | None]:
    fault_dir = _module_outdir(memory_name) / "faults"
    if fault_type == "stuck-at":
        return fault_dir / "sa0_faults.hex", fault_dir / "sa1_faults.hex"
    if fault_type == "transition-up":
        return fault_dir / "tf_up_faults.hex", None
    if fault_type == "transition-down":
        return fault_dir / "tf_down_faults.hex", None
    raise ValueError(f"Unsupported FAULT_TYPE: {fault_type}")


def _selected_fault_sites(*, fault_type: str, memory_name: str, addr_width: int, data_width: int) -> list[dict[str, int | str]]:
    file1, file2 = _fault_mask_paths(memory_name, fault_type)
    sites: list[dict[str, int | str]] = []

    if fault_type == "stuck-at":
        sa0_words = _read_words(file1)
        sa1_words = _read_words(file2) if file2 is not None else []
        for addr, (sa0_word, sa1_word) in enumerate(zip(sa0_words, sa1_words)):
            for bit in range(data_width):
                mask = 1 << bit
                sa0_bit = 1 if (sa0_word & mask) else 0
                sa1_bit = 1 if (sa1_word & mask) else 0
                if sa0_bit != sa1_bit:
                    continue
                sites.append(
                    {
                        "addr": addr,
                        "bit": bit,
                        "fault_value": sa0_bit,
                        "kind": f"SA{sa0_bit}",
                    }
                )
        return sites

    tf_words = _read_words(file1)
    for addr, tf_word in enumerate(tf_words):
        for bit in range(data_width):
            if tf_word & (1 << bit):
                sites.append(
                    {
                        "addr": addr,
                        "bit": bit,
                        "fault_value": -1,
                        "kind": "TF-UP" if fault_type == "transition-up" else "TF-DOWN",
                    }
                )
    return sites


def _detect_fault_rows(*, observations: list[dict[str, int]], selected_sites: list[dict[str, int | str]], fault_type: str, data_width: int) -> tuple[list[str], list[list[str]], int]:
    selected_by_location = {(int(site["addr"]), int(site["bit"])): site for site in selected_sites}
    detected_locations: set[tuple[int, int]] = set()
    rows: list[list[str]] = []
    headers = ["#", "TYPE", "ADDR", "BIT", "ACTUAL", "FAULT", "READ"]

    for observation in observations:
        addr = observation["addr"]
        if fault_type == "stuck-at":
            actual_word = observation["actual_word"]
            fault_word = observation["fault_word"]
            read_word = observation["read_word"]
            diff_word = actual_word ^ fault_word
            if diff_word == 0:
                continue
            for bit in range(data_width):
                if not (diff_word & (1 << bit)):
                    continue
                key = (addr, bit)
                if key not in selected_by_location or key in detected_locations:
                    continue
                detected_locations.add(key)
                site = selected_by_location[key]
                rows.append(
                    [
                        f"{len(rows) + 1}",
                        site["kind"],
                        f"0x{addr:04X}",
                        f"{bit}",
                        f"{(actual_word >> bit) & 1}",
                        f"{(fault_word >> bit) & 1}",
                        f"{(read_word >> bit) & 1}",
                    ]
                )
            continue

        headers = ["#", "TYPE", "ADDR", "BIT", "PREV", "ATTEMPT", "READ", "OBS"]
        prior_word = observation["prior_word"]
        attempted_word = observation["attempted_word"]
        read_word = observation["read_word"]
        blocked_bits = observation["blocked_bits"]

        for bit in range(data_width):
            if not (blocked_bits & (1 << bit)):
                continue
            key = (addr, bit)
            if key not in selected_by_location or key in detected_locations:
                continue
            attempted_bit = (attempted_word >> bit) & 1
            read_bit = (read_word >> bit) & 1
            if attempted_bit == read_bit:
                continue
            detected_locations.add(key)
            site = selected_by_location[key]
            rows.append(
                [
                    f"{len(rows) + 1}",
                    site["kind"],
                    f"0x{addr:04X}",
                    f"{bit}",
                    f"{(prior_word >> bit) & 1}",
                    f"{attempted_bit}",
                    f"{read_bit}",
                    "DELAYED",
                ]
            )

    return headers, rows, len(detected_locations)


def _report_fault_summary(
    *,
    observations: list[dict[str, int]],
    fault_type: str,
    memory_name: str,
    addr_width: int,
    data_width: int,
    fault_count: int,
    transition_stats: dict[str, object] | None = None,
) -> tuple[int, int, float]:
    selected_sites = _selected_fault_sites(
        fault_type=fault_type,
        memory_name=memory_name,
        addr_width=addr_width,
        data_width=data_width,
    )
    headers, rows, detected_count = _detect_fault_rows(
        observations=observations,
        selected_sites=selected_sites,
        fault_type=fault_type,
        data_width=data_width,
    )

    total_sites = len(selected_sites)
    coverage = 100.0 if total_sites == 0 else (detected_count / total_sites) * 100.0

    print()
    print("Fault summary")
    print(_format_ascii_table(
        headers,
        rows or ([["-", "-", "-", "-", "-", "-", "-", "-"]] if len(headers) == 8 else [["-", "-", "-", "-", "-", "-", "-"]]),
    ))
    print(f"Fault coverage: {detected_count}/{total_sites} ({coverage:.2f}%)")
    print(f"Injected faults: {fault_count}")

    if fault_type in {"transition-up", "transition-down"} and transition_stats is not None:
        print()
        print("Transition analysis")
        print(f"Transition model: delay-commit ({transition_stats.get('pulse_width_cycles', '?')} cycles)")
        print(f"Algorithm: {transition_stats.get('algo', 'unknown')}")
        direction = "0->1" if fault_type == "transition-up" else "1->0"
        print(f"Fault direction: {direction}")
        print(f"Writes with transition attempts: {transition_stats.get('blocked_write_cycles', 0)}")
        print(f"Read verifications: {transition_stats.get('read_verifications', 0)}")
        print(f"Detected delayed bits: {transition_stats.get('detected_bit_events', 0)}")
        print(f"Resolved-before-read bits: {transition_stats.get('resolved_before_read_bits', 0)}")
        print(f"Pending overwrites before read: {transition_stats.get('pending_overwrites', 0)}")
        print(f"Unverified pending writes at done: {transition_stats.get('unverified_pending_writes', 0)}")
        print(f"Max pending depth: {transition_stats.get('max_pending_depth', 0)}")
        blocked_by_addr = transition_stats.get("blocked_bits_by_addr", {})
        detected_by_addr = transition_stats.get("detected_bits_by_addr", {})
        if isinstance(blocked_by_addr, dict):
            print(f"Top blocked addresses (addr:bits): {_format_addr_bits_map(blocked_by_addr)}")
        if isinstance(detected_by_addr, dict):
            print(f"Top detected addresses (addr:bits): {_format_addr_bits_map(detected_by_addr)}")

    return detected_count, total_sites, coverage


def _prepare_fault_files() -> None:
    mode = os.getenv("FAULT_MODE", "clean").strip().lower()
    fault_type_raw = os.getenv("FAULT_TYPE", "stuck-at").strip().lower()
    addr_width = int(os.getenv("ADDR_WIDTH", "10"))
    data_width = int(os.getenv("DATA_WIDTH", "32"))
    faults = int(os.getenv("FAULTS", "0"))
    fault_seed_raw = os.getenv("FAULT_SEED", "")
    fault_seed = int(fault_seed_raw) if fault_seed_raw else None
    memory_name = os.getenv("MEMORY_NAME", "sram_1rw").strip() or "sram_1rw"

    if mode == "clean":
        faults = 0
    elif mode != "faults":
        raise ValueError(f"Unsupported FAULT_MODE: {mode}")

    fault_type_map = {
        "stuck-at": FaultType.STUCK_AT,
        "transition-up": FaultType.TRANSITION_UP,
        "transition-down": FaultType.TRANSITION_DOWN,
    }
    if fault_type_raw not in fault_type_map:
        raise ValueError(f"Unsupported FAULT_TYPE: {fault_type_raw}")

    # Consume the masks the generator already wrote, so the analysis masks are
    # identical to what the saboteur $readmemh-loads (reproducible, and correct
    # even when --seed is unset). Only (re)generate when they are missing, e.g. a
    # bare `make` run without a prior `autombist generate`.
    file1, file2 = _fault_mask_paths(memory_name, fault_type_raw)
    if file1.exists() and (file2 is None or file2.exists()):
        return
    generate_fault_files(
        outdir=_module_outdir(memory_name) / "faults",
        addr_width=addr_width,
        data_width=data_width,
        fault_type=fault_type_map[fault_type_raw],
        faults=faults,
        seed=fault_seed,
    )


_prepare_fault_files()


async def _run_mbist_once(
    dut,
    fault_type: str = "stuck-at",
    observations: list[dict[str, int]] | None = None,
    transition_stats: dict[str, object] | None = None,
) -> None:
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    seen_reads: set[tuple[int, ...]] = set()
    pending_transitions: dict[int, dict[str, int]] = {}
    pending_read_checks: list[dict[str, int]] = []
    pending_sa_checks: list[dict[str, int]] = []
    use_saboteur = os.getenv("USE_SABOTEUR", "1").strip().lower() not in {"0", "false", "no"}
    algo_name = os.getenv("ALGO", "march-c").strip().lower()
    fsm_name = "u_march_c_fsm" if algo_name == "march-c" else "u_march_raw_fsm"
    read_latency = int(os.getenv("READ_LATENCY", "1"))
    data_width = int(os.getenv("DATA_WIDTH", "32"))
    data_mask = (1 << data_width) - 1
    transition_masks: list[int] = []
    if fault_type in {"transition-up", "transition-down"}:
        memory_name = os.getenv("MEMORY_NAME", "sram_1rw").strip() or "sram_1rw"
        mask_path, _ = _fault_mask_paths(memory_name, fault_type)
        if mask_path.exists():
            transition_masks = _read_words(mask_path)
        if transition_stats is not None:
            transition_stats.setdefault("algo", algo_name)
            transition_stats.setdefault("pulse_width_cycles", int(os.getenv("PULSE_WIDTH_NS", "2")))
            transition_stats.setdefault("blocked_write_cycles", 0)
            transition_stats.setdefault("read_verifications", 0)
            transition_stats.setdefault("detected_bit_events", 0)
            transition_stats.setdefault("resolved_before_read_bits", 0)
            transition_stats.setdefault("pending_overwrites", 0)
            transition_stats.setdefault("unverified_pending_writes", 0)
            transition_stats.setdefault("max_pending_depth", 0)
            transition_stats.setdefault("blocked_bits_by_addr", {})
            transition_stats.setdefault("detected_bits_by_addr", {})

    dut.rst_n.value = 0
    dut.test_mode.value = 1
    dut.bist_start.value = 0

    dut.func_csb.value = 1
    dut.func_addr.value = 0
    dut.func_din.value = 0
    dut.func_we.value = 0

    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    dut.bist_start.value = 1
    await RisingEdge(dut.clk)
    dut.bist_start.value = 0

    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")

        if observations is None:
            if int(dut.bist_done.value) == 1:
                break
            continue

        if fault_type != "stuck-at" and pending_read_checks and use_saboteur:
            next_checks: list[dict[str, int]] = []
            for check in pending_read_checks:
                cycles_left = check["cycles_left"] - 1
                if cycles_left > 0:
                    check["cycles_left"] = cycles_left
                    next_checks.append(check)
                    continue

                read_word = _safe_int(dut.u_sram.dbg_actual_read)
                if read_word is None:
                    continue
                addr = check["addr"]
                prior_word = check["prior_word"]
                attempted_word = check["attempted_word"]
                blocked_bits = check["blocked_bits"]
                mismatch_bits = blocked_bits & (attempted_word ^ read_word)
                if transition_stats is not None:
                    transition_stats["read_verifications"] = int(transition_stats["read_verifications"]) + 1
                if mismatch_bits == 0:
                    if transition_stats is not None:
                        transition_stats["resolved_before_read_bits"] = int(transition_stats["resolved_before_read_bits"]) + _bitcount(blocked_bits)
                    continue

                key = (addr, prior_word, attempted_word, read_word, blocked_bits)
                if key in seen_reads:
                    continue
                if transition_stats is not None:
                    detected_count = _bitcount(mismatch_bits)
                    transition_stats["detected_bit_events"] = int(transition_stats["detected_bit_events"]) + detected_count
                    detected_by_addr = transition_stats.get("detected_bits_by_addr", {})
                    if isinstance(detected_by_addr, dict):
                        detected_by_addr[addr] = int(detected_by_addr.get(addr, 0)) + detected_count
                seen_reads.add(key)
                observations.append(
                    {
                        "addr": addr,
                        "prior_word": prior_word,
                        "attempted_word": attempted_word,
                        "read_word": read_word,
                        "blocked_bits": blocked_bits,
                    }
                )
            pending_read_checks = next_checks

        if fault_type == "stuck-at" and pending_sa_checks and use_saboteur:
            next_sa_checks: list[dict[str, int]] = []
            for check in pending_sa_checks:
                check["cycles_left"] -= 1
                if check["cycles_left"] > 0:
                    next_sa_checks.append(check)
                    continue
                actual_word = _safe_int(dut.u_sram.dbg_actual_word)
                fault_word = _safe_int(dut.u_sram.dbg_fault_word)
                if actual_word is None or fault_word is None:
                    continue
                sa_addr = check["addr"]
                key = (sa_addr, actual_word, fault_word)
                if key in seen_reads or actual_word == fault_word:
                    continue
                seen_reads.add(key)
                observations.append(
                    {
                        "addr": sa_addr,
                        "actual_word": actual_word,
                        "fault_word": fault_word,
                        "read_word": fault_word,
                    }
                )
            pending_sa_checks = next_sa_checks

        if int(dut.rst_n.value) == 1 and int(dut.test_mode.value) == 1:
            addr = _get_hier_value(dut, f"u_algo_top.{fsm_name}.mem_addr")
            mem_en = _get_hier_value(dut, f"u_algo_top.{fsm_name}.mem_en")
            mem_we = _get_hier_value(dut, f"u_algo_top.{fsm_name}.mem_we")

            if fault_type == "stuck-at":
                if use_saboteur and mem_en == 1 and mem_we == 0:
                    # Defer sampling until the faulted read data for this address is
                    # valid (the same point the MBIST FSM checks it). Sampling at the
                    # issue cycle reads stale/X data and misses every fault.
                    pending_sa_checks.append(
                        {"addr": addr, "cycles_left": max(read_latency + 1, 1)}
                    )
            elif use_saboteur:
                if mem_en == 1 and mem_we == 1:
                    prior_word = _safe_int(dut.u_sram.dbg_prior_value)
                    attempted_word = _safe_int(dut.u_sram.dbg_attempted_write)
                    dbg_blocked_bits = _safe_int(dut.u_sram.dbg_blocked_bits)
                    if addr in pending_transitions and transition_stats is not None:
                        transition_stats["pending_overwrites"] = int(transition_stats["pending_overwrites"]) + 1
                    if prior_word is not None and attempted_word is not None:
                        mask_word = transition_masks[addr] if addr < len(transition_masks) else 0
                        if fault_type == "transition-up":
                            blocked_bits = ((~prior_word) & attempted_word & mask_word) & data_mask
                        else:
                            blocked_bits = (prior_word & (~attempted_word) & mask_word) & data_mask
                        if dbg_blocked_bits is not None:
                            dbg_blocked_bits &= mask_word
                            if dbg_blocked_bits != 0:
                                blocked_bits = dbg_blocked_bits
                        pending_transitions[addr] = {
                            "prior_word": prior_word,
                            "attempted_word": attempted_word,
                            "blocked_bits": blocked_bits,
                        }
                        if transition_stats is not None and blocked_bits != 0:
                            transition_stats["blocked_write_cycles"] = int(transition_stats["blocked_write_cycles"]) + 1
                            blocked_count = _bitcount(blocked_bits)
                            blocked_by_addr = transition_stats.get("blocked_bits_by_addr", {})
                            if isinstance(blocked_by_addr, dict):
                                blocked_by_addr[addr] = int(blocked_by_addr.get(addr, 0)) + blocked_count
                        if transition_stats is not None:
                            current_depth = len(pending_transitions)
                            transition_stats["max_pending_depth"] = max(int(transition_stats["max_pending_depth"]), current_depth)
                elif mem_en == 1 and mem_we == 0:
                    pending = pending_transitions.pop(addr, None)
                    if pending is not None:
                        pending_read_checks.append(
                            {
                                "addr": addr,
                                "prior_word": pending["prior_word"],
                                "attempted_word": pending["attempted_word"],
                                "blocked_bits": pending["blocked_bits"],
                                "cycles_left": max(read_latency + 1, 1),
                            }
                        )

        if int(dut.bist_done.value) == 1 and not pending_read_checks and not pending_sa_checks:
            break

    if transition_stats is not None:
        transition_stats["unverified_pending_writes"] = len(pending_transitions)


@cocotb.test()
async def test_clean(dut):
    if os.getenv("FAULT_MODE", "clean").strip().lower() != "clean":
        return

    await _run_mbist_once(dut)
    assert int(dut.bist_fail.value) == 0, "MBIST reported fail in clean mode"


@cocotb.test()
async def test_faults(dut):
    if os.getenv("FAULT_MODE", "clean").strip().lower() != "faults":
        return
    if os.getenv("FAULT_TYPE", "stuck-at").strip().lower() != "stuck-at":
        return

    observations: list[dict[str, int]] = []
    await _run_mbist_once(dut, "stuck-at", observations)
    _report_fault_summary(
        observations=observations,
        fault_type="stuck-at",
        memory_name=os.getenv("MEMORY_NAME", "sram_1rw").strip() or "sram_1rw",
        addr_width=int(os.getenv("ADDR_WIDTH", "10")),
        data_width=int(os.getenv("DATA_WIDTH", "32")),
        fault_count=int(os.getenv("FAULTS", "0")),
    )
    assert int(dut.bist_fail.value) == 1, "MBIST did not detect injected faults"


@cocotb.test()
async def test_transition_faults(dut):
    if os.getenv("FAULT_MODE", "clean").strip().lower() != "faults":
        return

    fault_type = os.getenv("FAULT_TYPE", "stuck-at").strip().lower()
    if fault_type not in {"transition-up", "transition-down"}:
        return

    observations: list[dict[str, int]] = []
    transition_stats: dict[str, object] = {}
    await _run_mbist_once(dut, fault_type, observations, transition_stats)
    detected_count, total_sites, _ = _report_fault_summary(
        observations=observations,
        fault_type=fault_type,
        memory_name=os.getenv("MEMORY_NAME", "sram_1rw").strip() or "sram_1rw",
        addr_width=int(os.getenv("ADDR_WIDTH", "10")),
        data_width=int(os.getenv("DATA_WIDTH", "32")),
        fault_count=int(os.getenv("FAULTS", "0")),
        transition_stats=transition_stats,
    )

    algo_name = os.getenv("ALGO", "march-c").strip().lower()
    pulse_width_cycles = int(os.getenv("PULSE_WIDTH_NS", "2"))
    if algo_name == "march-raw":
        assert int(dut.bist_fail.value) == 1, "MBIST did not detect transition faults in March RAW"
    elif total_sites > 0 and pulse_width_cycles > 1:
        assert detected_count < total_sites, "March C unexpectedly detected 100% transition-delay faults"
