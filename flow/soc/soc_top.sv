`timescale 1ns/1ps
// soc_top: a minimal real SoC integration proof for openMBIST's on-chip
// self-repair -- a genuine RV32I CPU (PicoRV32, vendored under
// flow/soc/vendor/picorv32/, unmodified) fetches instructions from, and
// loads/stores through, TWO independently self-repair-wrapped memories.
// Both memories have a real, baked-in defect (flow/soc/sram_spares_soc_*.v).
//
// Boot sequencing mirrors how real BISR SoCs work: repair runs to
// completion in its own reset domain BEFORE the CPU is ever released out
// of reset, so the application core never observes the pre-repair, faulty
// memory state -- there is no window where a program could read a
// still-broken cell. `cpu_resetn` is gated off both memories' aggregate
// `self_repair_done` AND `self_repair_busy` clearing (see the comment at
// its assignment below for why `self_repair_done` alone isn't enough).
//
// Bus bridge: PicoRV32's native valid/ready memory interface is bridged to
// each memory's func_csb/func_we/func_addr/func_din/func_dout ports.
// Timing is NOT assumed -- it is derived directly from
// tests/hardware/sram_spares_intmem_a.v's own always_ff (address+we
// registered one cycle, dout registered a second cycle: a write commits on
// the edge that first samples it, so mem_ready can go high the very same
// cycle as mem_valid; a read's data is only valid starting the SECOND edge
// after the request is presented, so mem_ready waits two edges). The wrapper
// template (src/autombist/templates/wrapper_template.j2) confirms
// func_dout is a direct, unpipelined `assign func_dout = sram_dout;` passthrough
// in functional mode, so the underlying memory's own latency IS the
// func-port latency -- no hidden extra stage to account for.
//
// Word size is 32 bits throughout (DATA_WIDTH=32 on both memories) so no
// sub-word packing is needed; the test firmware (flow/soc/gen_program.py)
// only issues word-aligned lw/sw, so mem_wstrb is always either 4'b0000
// (read) or 4'b1111 (write) -- partial-strobe writes are out of scope.
module soc_top (
    input  logic clk,
    input  logic rst_n,              // resets memories + repair logic

    input  logic self_repair_start,  // level, held until self_repair_done reads back
    output logic self_repair_done,   // aggregate: both memories done
    output logic self_repair_fail,   // aggregate: either memory unrepairable
    output logic self_repair_busy,   // aggregate: either memory mid-repair

    output logic cpu_trap,           // picorv32's own illegal-state trap (must stay 0)
    output logic [31:0] status_reg   // firmware mailbox: PASS/FAIL signature lands here
);

    // DATA_BASE/STATUS_ADDR must match flow/soc/gen_program.py's Python
    // constants of the same name exactly -- hand-kept in sync, no shared
    // source of truth between the two files.
    localparam integer INSTR_ADDR_WIDTH = 6;             // 64 words
    localparam integer DATA_ADDR_WIDTH  = 5;             // 32 words
    localparam logic [31:0] INSTR_BYTES  = 32'h0000_0100; // 64 * 4
    localparam logic [31:0] DATA_BASE    = 32'h0000_1000;
    localparam logic [31:0] DATA_BYTES   = 32'h0000_0080; // 32 * 4
    localparam logic [31:0] STATUS_ADDR  = 32'h0000_2000;

    // ------------------------------------------------------------------
    // Two independent self-repair memories.
    // ------------------------------------------------------------------
    wire done_instr, fail_instr, busy_instr;
    wire done_data,  fail_data,  busy_data;

    assign self_repair_done = done_instr & done_data;
    assign self_repair_fail = fail_instr | fail_data;
    assign self_repair_busy = busy_instr | busy_data;

    // NOT just self_repair_done: onchip_selfrepair_ctrl's ctrl_test_mode_override
    // (which routes each wrapper's address/data mux to the MBIST path instead of
    // func_*) stays high for one extra cycle after self_repair_done first reads
    // 1 -- until ctrl_state actually reaches S_IDLE, which needs self_repair_start
    // observed low. Gating on self_repair_done alone let the CPU boot into that
    // one-cycle gap and fetch whatever the (by-then-idle) MBIST path was driving
    // instead of real memory content.
    //
    // This gate is only equivalent to "the mux is truly on the functional path"
    // because both memories are wired with func_we/test_mode(1'b0) below -- the
    // wrapper's real mux-select is `test_mode || ctrl_test_mode_override`
    // (wrapper_template.j2), which collapses to exactly self_repair_busy only
    // when test_mode is tied off. A future integration that also drives an
    // external tester's test_mode into these same wrapped memories would need a
    // different gate (or to expose effective_test_mode as a wrapper port).
    wire cpu_resetn = rst_n & self_repair_done & ~self_repair_busy;

    logic                       instr_csb;
    logic [INSTR_ADDR_WIDTH-1:0] instr_addr;
    logic [31:0]                instr_dout;

    logic                      data_csb, data_we;
    logic [DATA_ADDR_WIDTH-1:0] data_addr;
    logic [31:0]                data_din, data_dout;

    // Instruction memory is read-only from the bus by design (the firmware
    // never stores to the instruction region): func_we/func_din are tied off.
    selfrepair_soc_instr u_instr_mem (
        .clk(clk), .rst_n(rst_n),
        .test_mode(1'b0), .bist_start(1'b0), .bist_done(), .bist_fail(),
        .func_csb(instr_csb), .func_addr(instr_addr), .func_din(32'b0),
        .func_we(1'b0), .func_dout(instr_dout),
        .self_repair_start(self_repair_start),
        .self_repair_done(done_instr), .self_repair_fail(fail_instr), .self_repair_busy(busy_instr)
    );

    selfrepair_soc_data u_data_mem (
        .clk(clk), .rst_n(rst_n),
        .test_mode(1'b0), .bist_start(1'b0), .bist_done(), .bist_fail(),
        .func_csb(data_csb), .func_addr(data_addr), .func_din(data_din),
        .func_we(data_we), .func_dout(data_dout),
        .self_repair_start(self_repair_start),
        .self_repair_done(done_data), .self_repair_fail(fail_data), .self_repair_busy(busy_data)
    );

    // ------------------------------------------------------------------
    // PicoRV32 native memory bus.
    // ------------------------------------------------------------------
    wire        mem_valid, mem_instr;
    wire        mem_ready;
    wire [31:0] mem_addr, mem_wdata;
    wire [3:0]  mem_wstrb;
    wire [31:0] mem_rdata;

    picorv32 #(
        .ENABLE_COUNTERS(0), .ENABLE_COUNTERS64(0),
        .BARREL_SHIFTER(1),
        .COMPRESSED_ISA(0),
        .ENABLE_MUL(0), .ENABLE_DIV(0), .ENABLE_IRQ(0),
        .PROGADDR_RESET(32'h0000_0000),
        .PROGADDR_IRQ(32'h0000_0010),
        .STACKADDR(32'hffff_ffff)
    ) u_cpu (
        .clk(clk), .resetn(cpu_resetn),
        .trap(cpu_trap),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
        .mem_la_read(), .mem_la_write(), .mem_la_addr(), .mem_la_wdata(), .mem_la_wstrb(),
        .pcpi_valid(), .pcpi_insn(), .pcpi_rs1(), .pcpi_rs2(),
        .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
        .irq(32'b0), .eoi(),
        .trace_valid(), .trace_data()
    );

    // ------------------------------------------------------------------
    // Address decode + bus bridge (valid/ready <-> func_csb/we/addr/din).
    // ------------------------------------------------------------------
    // NOTE: sel_instr/sel_data/sel_status don't partition the full address
    // space -- any other word-aligned address falls through mem_rdata's mux
    // below to status_reg, acked the same cycle, with no bus-error signal.
    // The fixed firmware (flow/soc/gen_program.py) never generates such an
    // address, so this is dormant for this demo, not guarded against here.
    wire is_write   = |mem_wstrb;
    wire sel_instr  = mem_valid && (mem_addr < INSTR_BYTES);
    wire sel_data   = mem_valid && (mem_addr >= DATA_BASE) && (mem_addr < DATA_BASE + DATA_BYTES);
    wire sel_status = mem_valid && (mem_addr == STATUS_ADDR);

    assign instr_csb  = ~sel_instr;
    assign instr_addr = mem_addr[INSTR_ADDR_WIDTH+1:2];

    assign data_csb  = ~sel_data;
    assign data_we   = sel_data && is_write;
    assign data_addr = (mem_addr - DATA_BASE) >> 2;
    assign data_din  = mem_wdata;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            status_reg <= 32'b0;
        end else if (sel_status && is_write) begin
            status_reg <= mem_wdata;
        end
    end

    assign mem_rdata = sel_instr ? instr_dout : (sel_data ? data_dout : status_reg);

    // A read from either SRAM needs 2 clock edges before func_dout is valid
    // (address register stage, then output register stage -- see the
    // module header). Writes (to either SRAM) and any status_reg access
    // commit/read on the very same edge the request is first sampled, so
    // they need zero extra wait states.
    logic [1:0] wait_cnt;
    wire        sram_read_pending = (sel_instr || sel_data) && !is_write;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wait_cnt <= 2'd0;
        end else if (mem_valid && mem_ready) begin
            wait_cnt <= 2'd0;
        end else if (mem_valid && !mem_ready) begin
            wait_cnt <= wait_cnt + 2'd1;
        end else begin
            wait_cnt <= 2'd0;
        end
    end

    assign mem_ready = mem_valid && (sram_read_pending ? (wait_cnt == 2'd2) : (wait_cnt == 2'd0));

endmodule
