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


def _fault_mask_paths(memory_name: str, fault_type: str) -> tuple[Path, Path | None]:
    fault_dir = REPO_ROOT / "out" / memory_name / "faults"
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


def _detect_fault_rows(*, observations: list[dict[str, int]], selected_sites: list[dict[str, int | str]], fault_type: str, data_width: int) -> tuple[list[list[str]], int]:
    selected_by_location = {(int(site["addr"]), int(site["bit"])): site for site in selected_sites}
    detected_locations: set[tuple[int, int]] = set()
    rows: list[list[str]] = []

    for observation in observations:
        addr = observation["addr"]
        actual_word = observation["actual_word"]
        fault_word = observation["fault_word"]
        read_word = observation["read_word"]

        if fault_type == "stuck-at":
            diff_word = actual_word ^ fault_word
        else:
            diff_word = read_word ^ fault_word

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

    return rows, len(detected_locations)


def _report_fault_summary(*, observations: list[dict[str, int]], fault_type: str, memory_name: str, addr_width: int, data_width: int, fault_count: int) -> None:
    selected_sites = _selected_fault_sites(
        fault_type=fault_type,
        memory_name=memory_name,
        addr_width=addr_width,
        data_width=data_width,
    )
    rows, detected_count = _detect_fault_rows(
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
        ["#", "TYPE", "ADDR", "BIT", "ACTUAL", "FAULT", "READ"],
        rows or [["-", "-", "-", "-", "-", "-", "-"]],
    ))
    print(f"Fault coverage: {detected_count}/{total_sites} ({coverage:.2f}%)")
    print(f"Injected faults: {fault_count}")


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

    generate_fault_files(
        outdir=REPO_ROOT / "out" / memory_name / "faults",
        addr_width=addr_width,
        data_width=data_width,
        fault_type=fault_type_map[fault_type_raw],
        faults=faults,
        seed=fault_seed,
    )


_prepare_fault_files()


async def _run_mbist_once(dut, fault_type: str, observations: list[dict[str, int]] | None = None) -> None:
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    seen_reads: set[tuple[int, int, int, int]] = set()
    last_read_by_addr: dict[int, int] = {}
    use_saboteur = os.getenv("USE_SABOTEUR", "1").strip().lower() not in {"0", "false", "no"}
    algo_name = os.getenv("ALGO", "march-c").strip().lower()
    fsm_name = "u_march_c_fsm" if algo_name == "march-c" else "u_march_raw_fsm"

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

    while not int(dut.bist_done.value):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")

        if observations is None:
            continue
        if int(dut.rst_n.value) != 1 or int(dut.test_mode.value) != 1:
            continue

        addr = _get_hier_value(dut, f"u_algo_top.{fsm_name}.mem_addr")
        mem_we = _get_hier_value(dut, f"u_algo_top.{fsm_name}.mem_we")
        if addr is None:
            continue

        actual_word = _safe_int(dut.u_sram.dbg_actual_word) if use_saboteur else None
        if actual_word is None:
            continue

        if fault_type == "stuck-at":
            if mem_we != 0:
                continue
            read_word = _safe_int(dut.u_sram.dbg_fault_word)
            fault_word = read_word
            if read_word is None:
                continue
            last_read_by_addr[addr] = read_word
            key = (addr, actual_word, fault_word, read_word)
            if key in seen_reads or actual_word == fault_word:
                continue
            seen_reads.add(key)
            observations.append(
                {
                    "addr": addr,
                    "actual_word": actual_word,
                    "fault_word": fault_word,
                    "read_word": read_word,
                }
            )
        else:
            if mem_we != 1:
                continue
            read_word = _safe_int(dut.u_sram.dbg_intended_word)
            fault_word = _safe_int(dut.u_sram.dbg_fault_word)
            if read_word is None or fault_word is None:
                continue
            cached_actual = last_read_by_addr.get(addr)
            if cached_actual is None:
                cached_actual = actual_word
            actual_word = cached_actual
            key = (addr, actual_word, fault_word, read_word)
            if key in seen_reads or read_word == fault_word:
                continue
            seen_reads.add(key)
            observations.append(
                {
                    "addr": addr,
                    "actual_word": actual_word,
                    "fault_word": fault_word,
                    "read_word": read_word,
                }
            )


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
    await _run_mbist_once(dut, fault_type, observations)
    _report_fault_summary(
        observations=observations,
        fault_type=fault_type,
        memory_name=os.getenv("MEMORY_NAME", "sram_1rw").strip() or "sram_1rw",
        addr_width=int(os.getenv("ADDR_WIDTH", "10")),
        data_width=int(os.getenv("DATA_WIDTH", "32")),
        fault_count=int(os.getenv("FAULTS", "0")),
    )
    assert int(dut.bist_fail.value) == 1, "MBIST did not detect transition faults"
