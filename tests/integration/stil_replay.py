"""Replay a STIL file literally against RTL in Icarus Verilog: every drive at its time,
every compare at its strobe, as a tester would apply them.

It reads the STIL text itself, independently of whatever wrote it (warptap's to_stil):
the Signals, each WaveformTable's period and per-character events, and the Pattern's
``W``, ``V`` and ``Loop`` statements. A signal keeps its waveform character until a
``V`` changes it, as STIL specifies. Events: ``D``/``U`` drive 0/1, ``H``/``L`` compare
1/0, ``X``/``P``/``N``/``Z`` do nothing here. That is the subset to_stil writes; anything
else raises, rather than being skipped.

A test helper, not a test module (no ``test_`` prefix, so pytest does not collect it).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_UNIT_NS = {"fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}
_NAME = r'("[^"]+"|[A-Za-z_]\w*)'


class StilReplayError(ValueError):
    pass


@dataclass(frozen=True)
class Compare:
    vector: int
    signal: str
    expected: int


@dataclass(frozen=True)
class StilReplay:
    compares: list[Compare]
    mismatches: list[int]  # indices into compares
    signals: dict[str, str]  # name -> In/Out
    output: str

    @property
    def passed(self) -> bool:
        return not self.mismatches


def _unquote(name: str) -> str:
    return name[1:-1] if name.startswith('"') else name


def _time_ns(text: str) -> float:
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(fs|ps|ns|us|ms|s)\s*", text)
    if m is None:
        raise StilReplayError(f"can't read time {text!r}")
    return float(m.group(1)) * _UNIT_NS[m.group(2)]


def _block(text: str, keyword: str) -> str:
    """The body of the first top-level ``keyword ... { ... }`` block, braces balanced."""
    start = re.search(rf"^{keyword}\b[^{{]*\{{", text, re.MULTILINE)
    if start is None:
        raise StilReplayError(f"no {keyword} block")
    depth, i = 1, start.end()
    while depth:
        c = text[i]
        depth += c == "{"
        depth -= c == "}"
        i += 1
    return text[start.end():i - 1]


def parse_signals(stil: str) -> dict[str, str]:
    # [ \t] rather than \s throughout: a match must stay on its own line
    return {_unquote(n): d for n, d in re.findall(rf"^[ \t]*{_NAME}[ \t]+(In|Out|InOut)[ \t]*;", _block(stil, "Signals"), re.MULTILINE)}


def parse_tables(stil: str) -> dict[str, tuple[float, dict[str, tuple[str, list[tuple[float, str]]]]]]:
    tables = {}
    timing = _block(stil, "Timing")
    for m in re.finditer(r"WaveformTable\s+(\w+)\s*\{", timing):
        name = m.group(1)
        rest = timing[m.end():]
        nxt = re.search(r"WaveformTable\s+\w+\s*\{", rest)
        body = rest[: nxt.start()] if nxt else rest
        period = _time_ns(re.search(r"Period\s+'([^']+)'", body).group(1))
        waves = {}
        for sig, wfcs, evs in re.findall(rf"^[ \t]*{_NAME}[ \t]*\{{[ \t]*(\w+)[ \t]*\{{(.*)\}}\}}[ \t]*$", body, re.MULTILINE):
            events = [(_time_ns(t), e) for t, e in re.findall(r"'([^']+)'\s+([A-Z/]+)\s*;", evs)]
            waves[_unquote(sig)] = (wfcs, events)
        tables[name] = (period, waves)
    return tables


def parse_vectors(stil: str) -> list[tuple[str, dict[str, str]]]:
    """``(table, waveform character per signal)`` per vector, loops expanded."""
    body = _block(stil, r"Pattern\s+\w+")
    tokens = re.findall(r"W\s+(\w+)\s*;|V\s*\{([^}]*)\}|Loop\s+(\d+)\s*\{|(\})", body)
    stack: list[tuple[int, list]] = [(1, [])]
    for table, vec, loop, close in tokens:
        if table:
            stack[-1][1].append(("W", table))
        elif loop:
            stack.append((int(loop), []))
        elif close:
            count, items = stack.pop()
            stack[-1][1].extend(items * count)
        else:
            stack[-1][1].append(("V", {_unquote(n): c for n, c in re.findall(rf"{_NAME}\s*=\s*(\w)\s*;", vec)}))
    if len(stack) != 1:
        raise StilReplayError("unbalanced Loop braces in the Pattern")
    vectors, table, wfc = [], None, {}
    for kind, item in stack[0][1]:
        if kind == "W":
            table = item
        else:
            wfc.update(item)  # a signal keeps its character until a V changes it
            if table is None:
                raise StilReplayError("a V before any W")
            vectors.append((table, dict(wfc)))
    return vectors


def replay_stil(stil: str, verilog_files: list[Path], top: str, ports: list, workdir: Path,
                iverilog: str = "iverilog", vvp: str = "vvp") -> StilReplay:
    """Simulate ``top`` (``ports`` from ``jtag_bist.parse_netlist_ports``) under the STIL."""
    signals = parse_signals(stil)
    tables = parse_tables(stil)
    drives: list[tuple[float, str, int]] = []
    compares: list[tuple[float, Compare]] = []
    t = 0.0
    last: dict[str, int] = {}
    for index, (table, wfc) in enumerate(parse_vectors(stil)):
        period, waves = tables[table]
        for sig, char in wfc.items():
            chars, events = waves[sig]
            pick = chars.index(char)
            for at, event in events:
                choice = event.split("/")[pick] if "/" in event else event
                if choice in ("D", "U"):
                    value = int(choice == "U")
                    if last.get(sig) != value:
                        drives.append((t + at, sig, value))
                        last[sig] = value
                elif choice in ("H", "L"):
                    compares.append((t + at, Compare(index, sig, int(choice == "H"))))
                elif choice not in ("X", "P", "N", "Z"):
                    raise StilReplayError(f"event {choice!r} is not one this replay applies")
        t += period

    decl, conn = [], []
    for p in ports:
        decl.append(f"    {'reg' if p.direction == 'input' else 'wire'} {p.range}{p.name};")
        conn.append(f".{p.name}({p.name})")
    events = sorted([(at, 0, sig, v) for at, sig, v in drives] + [(at, 1, k, c) for k, (at, c) in enumerate(compares)],
                    key=lambda e: (e[0], e[1]))
    lines = ["`timescale 1ns/1ps", "module tb_stil_replay;", *decl, "    integer mismatches = 0;",
             f"    {top} dut ({', '.join(conn)});", "    initial begin"]
    now = 0.0
    for at, kind, a, b in events:
        if at > now:
            lines.append(f"        #{at - now:.4f};")
            now = at
        if kind == 0:
            lines.append(f"        {a} = 1'b{b};")
        else:
            lines.append(f"        if ({b.signal} !== 1'b{b.expected}) begin mismatches = mismatches + 1; "
                         f"$display(\"MISMATCH {a}\"); end")
    lines += ["        #1;", "        $display(\"REPLAY DONE %0d\", mismatches);", "        $finish;", "    end",
              "endmodule", ""]
    workdir.mkdir(parents=True, exist_ok=True)
    tb = workdir / "tb_stil_replay.v"
    tb.write_text("\n".join(lines), encoding="utf-8")
    sim = workdir / "stil_replay.vvp"
    built = subprocess.run([iverilog, "-g2012", "-s", "tb_stil_replay", "-o", str(sim), str(tb), *map(str, verilog_files)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        raise StilReplayError(f"compile failed:\n{built.stderr}")
    ran = subprocess.run([vvp, "-n", str(sim)], capture_output=True, text=True)
    if "REPLAY DONE" not in ran.stdout:
        raise StilReplayError(f"simulation did not finish:\n{ran.stdout}\n{ran.stderr}")
    return StilReplay(
        compares=[c for _, c in compares],
        mismatches=[int(m) for m in re.findall(r"^MISMATCH (\d+)$", ran.stdout, re.MULTILINE)],
        signals=signals,
        output=ran.stdout,
    )
