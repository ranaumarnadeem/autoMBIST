`timescale 1ns/1ps
// soc_top_hw: the flow/soc RV32I self-repair demo, retargeted onto the REAL
// hardened OpenRAM sky130 macros from flow/multimem/mbist/ instead of
// flow/soc's toy defect-injectable behavioral fixtures.
//
// Reuses selfrepair_a/selfrepair_b UNCHANGED -- same module names, same
// generator config (redundancy: {num_spare_rows: 1, onchip_selfrepair: true},
// algo march-c) as flow/multimem/mbist/mem_subsystem_mbist.sv, which already
// hardens clean (0 DRT, LVS-clean incl. power). This file only adds a real
// RV32I CPU driving that same proven wrapper/macro pair through real
// fetch/load/store traffic, instead of the plain functional bus
// mem_subsystem_mbist.sv uses.
//
//   slot 0 (instruction): selfrepair_a -> sram_wrap_a -> sky130_sram_32b256w (256w)
//   slot 1 (data):        selfrepair_b -> sram_wrap_b -> sky130_sram_32b512w (512w)
//
// Real macros have NO defect-injection knob (pre-silicon, per this project's
// established limit) -- self-repair here runs onchip_selfrepair's BIST at
// boot as always, finds nothing to repair (self_repair_fail stays 0, no row
// ever gets remapped), and the CPU boots normally afterward. This is a
// PHYSICAL/toolchain-closure target (can the whole CPU+repair+real-macro
// design synthesize, place, route, and sign off through LibreLane?), not a
// second defect-correction proof -- flow/soc/soc_top.sv already covers that
// with defect-injectable fixtures.
//
// BUS TIMING DIFFERS FROM flow/soc/soc_top.sv -- read this before touching
// wait_cnt: the real macro model (flow/multimem/sky130_sram_32b256w.v) is
// NOT the same shape as the toy fixtures. Toy fixtures register the address
// on one posedge and the output on a SECOND posedge (2-cycle latency). The
// real OpenRAM model instead latches all inputs (blocking, so effectively
// immediate) on the posedge, then commits writes and captures dout0 on the
// FOLLOWING NEGEDGE (`always @(negedge clk0)`) -- so from a purely
// posedge-sampling observer (which is all a picorv32-driven bridge ever is),
// dout0 is already stable and correct by the NEXT posedge: ONE cycle of
// apparent latency, not two. Using wait_cnt==2 here (copy-pasting
// flow/soc/soc_top.sv's constant unchanged) would be silently wrong --
// mem_ready would pulse a cycle late against data that's already been stable
// for a full cycle, not a functional bug, but an unnecessary/undocumented
// assumption this comment exists specifically to prevent.  Writes commit at
// the same negedge using the posedge-latched address/data, so a new request's
// combinational bus values (which could already differ by the time that
// negedge fires) can't race it -- 0-wait writes remain correct, same as
// flow/soc/soc_top.sv.
module soc_top_hw (
    input  logic clk,
    input  logic rst_n,              // resets memories + repair logic

    input  logic self_repair_start,  // level, held until self_repair_done reads back
    output logic self_repair_done,   // aggregate: both memories done
    output logic self_repair_fail,   // aggregate: either memory unrepairable
    output logic self_repair_busy,   // aggregate: either memory mid-repair

    output logic cpu_trap,           // picorv32's own illegal-state trap (must stay 0)
    output logic [31:0] status_reg   // firmware mailbox: PASS/FAIL signature lands here
);

    // Must match flow/soc/gen_program.py's Python constants of the same name
    // exactly -- hand-kept in sync, no shared source of truth between files.
    localparam integer INSTR_ADDR_WIDTH = 8;              // 256 words (sram_wrap_a)
    localparam integer DATA_ADDR_WIDTH  = 9;              // 512 words (sram_wrap_b)
    localparam logic [31:0] INSTR_BYTES  = 32'h0000_0400; // 256 * 4
    localparam logic [31:0] DATA_BASE    = 32'h0000_1000;
    localparam logic [31:0] DATA_BYTES   = 32'h0000_0800; // 512 * 4
    localparam logic [31:0] STATUS_ADDR  = 32'h0000_2000;

    // ------------------------------------------------------------------
    // Two independent self-repair memories, wrapping the REAL macros.
    // ------------------------------------------------------------------
    wire done_instr, fail_instr, busy_instr;
    wire done_data,  fail_data,  busy_data;

    assign self_repair_done = done_instr & done_data;
    assign self_repair_fail = fail_instr | fail_data;
    assign self_repair_busy = busy_instr | busy_data;

    // See flow/soc/soc_top.sv's identical gate for why self_repair_done alone
    // isn't enough (ctrl_test_mode_override / self_repair_busy identity), and
    // why this is only sound because test_mode is tied to 0 below.
    wire cpu_resetn = rst_n & self_repair_done & ~self_repair_busy;

    logic                       instr_csb;
    logic [INSTR_ADDR_WIDTH-1:0] instr_addr;
    logic [31:0]                instr_dout;

    logic                      data_csb, data_we;
    logic [DATA_ADDR_WIDTH-1:0] data_addr;
    logic [31:0]                data_din, data_dout;

    // Instruction memory is read-only from the bus by design (the firmware
    // never stores to the instruction region): func_we/func_din are tied off.
    selfrepair_a u_instr_mem (
        .clk(clk), .rst_n(rst_n), .test_mode(1'b0), .bist_start(1'b0), .bist_done(), .bist_fail(),
        .func_csb(instr_csb), .func_addr(instr_addr), .func_din(32'b0),
        .func_we(1'b0), .func_dout(instr_dout),
        .self_repair_start(self_repair_start),
        .self_repair_done(done_instr), .self_repair_fail(fail_instr), .self_repair_busy(busy_instr)
    );

    selfrepair_b u_data_mem (
        .clk(clk), .rst_n(rst_n), .test_mode(1'b0), .bist_start(1'b0), .bist_done(), .bist_fail(),
        .func_csb(data_csb), .func_addr(data_addr), .func_din(data_din),
        .func_we(data_we), .func_dout(data_dout),
        .self_repair_start(self_repair_start),
        .self_repair_done(done_data), .self_repair_fail(fail_data), .self_repair_busy(busy_data)
    );

    // ------------------------------------------------------------------
    // PicoRV32 native memory bus (unmodified core, flow/soc/vendor/picorv32/).
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

    // Real macro: dout is already stable one posedge after the read request
    // is first presented (see module header) -- wait_cnt==1, NOT 2. Writes
    // (either memory) and any status_reg access still commit/read on the
    // very same edge the request is first sampled -- zero extra wait states.
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

    assign mem_ready = mem_valid && (sram_read_pending ? (wait_cnt == 2'd1) : (wait_cnt == 2'd0));

endmodule
