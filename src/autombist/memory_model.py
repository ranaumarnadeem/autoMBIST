"""A behavioral simulation model of the memory a generated wrapper instantiates,
so the testbench `generate` writes (tb/) can run the BIST with no memory model
supplied.

The ports are the ones ``manifest.render_memory_stub`` declares for the same
config (the stub is the single source of truth for what the wrapper connects);
this adds behavior. It is idealized: no timing checks, contents power up unknown,
and it is fault-free. It is NOT the macro's own model -- the testbench's run
script replaces it with the macro's model when one is passed.

Read timing follows ``read_latency``: 0 is the style of a real OpenRAM macro
(inputs registered on the rising edge, the write and the read done on the
falling edge, so read data is valid before the next rising edge, where a
latency-0 controller samples it); 1 or more is the registered-read style of the
repo's other ``sram_*`` models (data one clock after the registered access,
held until the next read, which also satisfies any larger latency).

On a column-muxed memory (``redundancy.words_per_row > 1``) with spare columns,
a spare lane is ONE cell per physical row, shared by the row's words, as
OpenRAM builds it (its spare columns bypass the column mux). The model keeps it
at the row's first address: every word of the row writes and reads the same
spare cell. (OpenRAM's own behavioral ``.v`` gives each word its own, which
would hide a remap that writes the spare from the wrong word.) On two ports
the cell is shared by both, and a read's spare lane is X while the other port
writes that row's cell in the same cycle, as a dual-port bitcell leaves it.
"""
from __future__ import annotations

from typing import Any

from .manifest import _geometry, _memory_connection_widths, _ports, _redundancy_flags


class MemoryModelError(ValueError):
    """The config describes a memory this model cannot be derived for."""

    __test__ = False


def render_memory_model(config: dict[str, Any], *, stuck_masks: tuple[str, str] | None = None) -> str:
    """Verilog for a behavioral model named ``config['memory_name']`` with exactly
    the ports (names, directions, widths) of ``render_memory_stub(config)``.

    ``stuck_masks`` (an AND-mask file and an OR-mask file, absolute paths) makes it
    fault-injectable: every read returns ``(stored & sa0) | sa1`` for the word it
    reads, over the PHYSICAL array, spare rows and spare lanes included. The masks
    are ``$readmemh``-loaded when the simulation starts, so rewriting the files
    changes the defects without recompiling."""
    geo = _geometry(config)
    flags = _redundancy_flags(config)
    addr_w, data_w = _memory_connection_widths(config, flags)
    logical_w = geo["data_width"]
    lanes = data_w - logical_w
    if lanes < 0:
        raise MemoryModelError(
            f"the memory is connected {data_w} data bits but the config's data_width is {logical_w}"
        )
    spare_w = max(1, geo["num_spare_cols"])
    # Column bits whose words share one spare cell (0: each word has its own).
    col_bits = (geo["words_per_row"] - 1).bit_length() if lanes else 0
    latency = geo["read_latency"]
    we_active_low = bool(config.get("we_active_low", True))
    ports = list(_ports(config).values())

    # Declarations in the stub's order; a pin that serves the same role on several
    # ports (a shared clock) is declared once.
    decls: dict[str, str] = {}

    def add(name: str, decl: str) -> None:
        decls.setdefault(name, decl)

    for pdata in ports:
        ptype = pdata["type"]
        add(pdata["clk"], f"input  wire {pdata['clk']}")
        add(pdata["csb"], f"input  wire {pdata['csb']}")
        if ptype in ("w", "rw"):
            add(pdata["we"], f"input  wire {pdata['we']}")
        add(pdata["addr"], f"input  wire [{addr_w - 1}:0] {pdata['addr']}")
        if ptype in ("w", "rw"):
            add(pdata["din"], f"input  wire [{data_w - 1}:0] {pdata['din']}")
        if pdata.get("spare_wen"):
            add(pdata["spare_wen"], f"input  wire [{spare_w - 1}:0] {pdata['spare_wen']}")
        if ptype in ("r", "rw"):
            add(pdata["dout"], f"output reg  [{data_w - 1}:0] {pdata['dout']}")

    # A compiled multi-port macro (OpenRAM's among them) does not define a read of the
    # address another port writes in the same cycle, so neither does this model: such a
    # read returns X, and a controller that relies on one fails here instead of on
    # silicon. The access is identified from the ports' registered values, which hold
    # the cycle the read was issued in for both read styles.
    collide = len(ports) > 1
    body: list[str] = []
    if col_bits:
        body += _shared_word_function(addr_w=addr_w, data_w=data_w, col_bits=col_bits,
                                      masked=stuck_masks is not None)
    for i, pdata in enumerate(ports):
        body += _port_behavior(i, pdata, addr_w=addr_w, data_w=data_w, lanes=lanes,
                               latency=latency, we_active_low=we_active_low, collide=collide,
                               masked=stuck_masks is not None, col_bits=col_bits)
    if collide:
        for i, pdata in enumerate(ports):
            if pdata["type"] in ("r", "rw"):
                body += _read_function(i, ports, addr_w=addr_w, data_w=data_w,
                                       we_active_low=we_active_low, lanes=lanes, col_bits=col_bits)

    collision = ", a read of an address another port writes in the same cycle X" if collide else ""
    style = (
        "inputs registered on the rising clock edge, the write and the read done on the "
        f"falling edge{collision} (read_latency 0)"
        if latency == 0
        else "writes on the rising edge, reads one clock after the registered access, data held "
        f"until the next read{collision} (read_latency {latency})"
    )
    shared = (
        [f"// Column-muxed, {geo['words_per_row']} words per row: each spare lane is one cell per physical",
         "// row, shared by the row's words and kept at its first address (OpenRAM's layout)."]
        if col_bits else []
    )
    lines = [
        "`timescale 1ns/1ps",
        "// Auto-generated by autombist from config.yml: a BEHAVIORAL model of the memory,",
        f"// {style}.",
        *(_INJECTABLE_HEADER if stuck_masks else _FAULT_FREE_HEADER),
        *shared,
        f"module {config['memory_name']} #(",
        f"    parameter integer ADDR_WIDTH = {geo['addr_width']},",
        f"    parameter integer DATA_WIDTH = {geo['data_width']},",
        f"    parameter integer NUM_SPARE_ROWS = {geo['num_spare_rows']},",
        f"    parameter integer NUM_SPARE_COLS = {geo['num_spare_cols']}",
        ") (",
        ",\n".join(f"    {d}" for d in decls.values()),
        ");",
        "    // The wrapper overrides the parameters above; the widths below are the ones it connects.",
        f"    localparam integer LOGICAL_W = {logical_w};",
        f"    localparam integer DEPTH = {1 << addr_w};",
        f"    reg [{data_w - 1}:0] mem [0:DEPTH-1];",
        *(_mask_lines(data_w, stuck_masks) if stuck_masks else []),
        "",
        *body,
        "endmodule",
        "",
    ]
    return "\n".join(lines)


_FAULT_FREE_HEADER = [
    "// Idealized: fault-free, no timing checks, contents power up unknown. It is NOT the",
    "// macro's own model; pass that model to run_tb.sh to replace this one.",
]
_INJECTABLE_HEADER = [
    "// FAULT-INJECTABLE: every read returns (stored & sa0_mask) | sa1_mask over the physical",
    "// array, spare rows and spare lanes included; the masks load from files at time 0, so",
    "// rewriting them changes the defects without recompiling. Otherwise idealized: no",
    "// timing checks, contents power up unknown. It is NOT the macro's own model.",
]


def _mask_lines(data_w: int, stuck_masks: tuple[str, str]) -> list[str]:
    sa0, sa1 = stuck_masks
    return [
        f"    reg [{data_w - 1}:0] sa0_mask [0:DEPTH-1];",
        f"    reg [{data_w - 1}:0] sa1_mask [0:DEPTH-1];",
        "    initial begin",
        f'        $readmemh("{sa0}", sa0_mask);',
        f'        $readmemh("{sa1}", sa1_mask);',
        "    end",
    ]


def _terms(has_we: bool, we: str | None, we_active_low: bool) -> tuple[str, str]:
    """(write, read) conditions on a ``we`` value: a write-only port always writes
    and a read-only port always reads."""
    if not has_we:
        return "1'b0", "1'b1"
    return (f"!{we}", we) if we_active_low else (we, f"!{we}")


def _row_base(addr: str, addr_w: int, col_bits: int) -> str:
    """The first address of ``addr``'s physical row: its column bits cleared."""
    return f"{{{addr}[{addr_w - 1}:{col_bits}], {col_bits}'b0}}"


def _store(addr: str, din: str, spare_wen: str | None, lanes: int, k: str,
           spare_addr: str | None = None) -> list[str]:
    """Statements writing ``din`` to ``mem[addr]``: the logical bits always, and a
    spare lane only under its ``spare_wen`` bit (a spare lane is never written as a
    side effect of a word store), into ``mem[spare_addr]`` when the spare cell is
    shared by a physical row."""
    if lanes == 0 or spare_wen is None:
        return [f"mem[{addr}] <= {din};"]
    spare = spare_addr or addr
    return [
        f"mem[{addr}][LOGICAL_W-1:0] <= {din}[LOGICAL_W-1:0];",
        f"for ({k} = 0; {k} < {lanes}; {k} = {k} + 1)",
        f"    if ({spare_wen}[{k}]) mem[{spare}][LOGICAL_W + {k}] <= {din}[LOGICAL_W + {k}];",
    ]


def _shared_word_function(*, addr_w: int, data_w: int, col_bits: int, masked: bool) -> list[str]:
    """``word_at(a)``: the word at ``a`` with its spare lanes read from the row's
    first address, where the row's one spare cell per lane is kept; each half
    masked at its own address when the model is fault-injectable."""
    out = [
        "    // The word at a: its logical bits from mem[a], its spare lanes from the row's",
        "    // first address, the one spare cell its physical row has.",
        f"    function [{data_w - 1}:0] word_at(input [{addr_w - 1}:0] a);",
        f"        reg [{addr_w - 1}:0] b;",
        f"        reg [{data_w - 1}:0] w, s;",
        "        begin",
        f"            b = {_row_base('a', addr_w, col_bits)};",
        "            w = mem[a];",
        "            s = mem[b];",
    ]
    if masked:
        out += ["            w = (w & sa0_mask[a]) | sa1_mask[a];",
                "            s = (s & sa0_mask[b]) | sa1_mask[b];"]
    out += [f"            word_at = {{s[{data_w - 1}:LOGICAL_W], w[LOGICAL_W-1:0]}};",
            "        end", "    endfunction", ""]
    return out


def _read_function(
    r: int, ports: list[dict[str, Any]], *, addr_w: int, data_w: int, we_active_low: bool,
    lanes: int = 0, col_bits: int = 0,
) -> list[str]:
    """``p<r>_read(a)``: the stored word at ``a``, or X when another port wrote ``a`` in
    the cycle this read was issued (its registered csb/we/addr are that cycle's).

    On a column-muxed memory the read's spare lanes are the row's shared cells
    (``word_at``), and a lane is X when another port wrote that cell in the same
    cycle -- a write to ANY word of the row with that lane's spare_wen raised."""
    p = f"p{r}"
    out = [
        f"    // port {r}'s read: X if another port wrote the same address in the same cycle",
        f"    function [{data_w - 1}:0] {p}_read(input [{addr_w - 1}:0] a);",
    ]
    if col_bits:
        out.append("        integer k;")
    out += ["        begin", f"            {p}_read = {'word_at(a)' if col_bits else 'mem[a]'};"]
    for w, wdata in enumerate(ports):
        if w == r or wdata["type"] not in ("w", "rw"):
            continue
        q = f"p{w}"
        write_q, _ = _terms(True, f"{q}_we_q", we_active_low)
        if col_bits and wdata.get("spare_wen"):
            same_row = f"{q}_addr_q[{addr_w - 1}:{col_bits}] == a[{addr_w - 1}:{col_bits}]"
            out += [
                f"            if (!{q}_csb_q && {write_q} && {same_row})",
                f"                for (k = 0; k < {lanes}; k = k + 1)",
                f"                    if ({q}_spare_q[k]) {p}_read[LOGICAL_W + k] = 1'bx;",
            ]
        out.append(f"            if (!{q}_csb_q && {write_q} && {q}_addr_q == a) {p}_read = {{{data_w}{{1'bx}}}};")
    out += ["        end", "    endfunction", ""]
    return out


def _port_behavior(
    i: int, pdata: dict[str, Any], *, addr_w: int, data_w: int, lanes: int, latency: int,
    we_active_low: bool, collide: bool = False, masked: bool = False, col_bits: int = 0,
) -> list[str]:
    p = f"p{i}"
    ptype = pdata["type"]
    has_we = ptype in ("w", "rw")
    does_read = ptype in ("r", "rw")
    clk, csb, addr = pdata["clk"], pdata["csb"], pdata["addr"]
    we, din, dout = pdata.get("we"), pdata.get("din"), pdata.get("dout")
    spare_wen = pdata.get("spare_wen") if lanes else None
    k = f"{p}_k"
    write_live, _ = _terms(has_we, we, we_active_low)
    write_q, read_q = _terms(has_we, f"{p}_we_q" if has_we else None, we_active_low)
    # The registered spare_wen: latency 0 stores from it; on a multi-port
    # column-muxed memory the other ports' reads also check it for a write to
    # their row's shared spare cell (_read_function).
    spare_q = bool(spare_wen) and (latency == 0 or (collide and col_bits > 0))

    out = [f"    // port {i}: {ptype}", f"    reg {p}_csb_q;", f"    reg [{addr_w - 1}:0] {p}_addr_q;"]
    if has_we:
        out.append(f"    reg {p}_we_q;")
        if latency == 0:
            out.append(f"    reg [{data_w - 1}:0] {p}_din_q;")
        if spare_q:
            out.append(f"    reg [{lanes - 1}:0] {p}_spare_q;")
        if spare_wen:
            out.append(f"    integer {k};")

    out.append(f"    always @(posedge {clk}) begin")
    out += [f"        {p}_csb_q  <= {csb};", f"        {p}_addr_q <= {addr};"]
    if has_we:
        out.append(f"        {p}_we_q   <= {we};")
        if latency == 0:
            out.append(f"        {p}_din_q  <= {din};")
    if spare_q:
        out.append(f"        {p}_spare_q <= {spare_wen}[{lanes - 1}:0];")
    if latency == 0:
        out.append("    end")
        out.append(f"    always @(negedge {clk}) begin")
        if has_we:
            out.append(f"        if (!{p}_csb_q && {write_q}) begin")
            out += [f"            {s}" for s in _store(
                f"{p}_addr_q", f"{p}_din_q", f"{p}_spare_q" if spare_wen else None, lanes, k,
                _row_base(f"{p}_addr_q", addr_w, col_bits) if col_bits else None)]
            out.append("        end")
    elif has_we:
        out.append(f"        if (!{csb} && {write_live}) begin")
        out += [f"            {s}" for s in _store(addr, din, spare_wen, lanes, k,
                                                    _row_base(addr, addr_w, col_bits) if col_bits else None)]
        out.append("        end")
    if does_read:
        if collide:  # _read_function reads the row's shared spare cells itself
            word = f"{p}_read({p}_addr_q)"
        else:
            word = f"word_at({p}_addr_q)" if col_bits else f"mem[{p}_addr_q]"
        if masked and not col_bits:  # word_at masks each half at its own address
            word = f"(({word}) & sa0_mask[{p}_addr_q]) | sa1_mask[{p}_addr_q]"
        out.append(f"        if (!{p}_csb_q && {read_q}) {dout} <= {word};")
    out += ["    end", ""]
    return out
