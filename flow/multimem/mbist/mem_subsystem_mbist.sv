`timescale 1ns/1ps
// -----------------------------------------------------------------------------
// mem_subsystem_mbist: the flow/multimem 3-memory subsystem with autonomous
// on-chip self-repair MBIST wrapped around EACH memory.
//
// Every memory is an autombist-generated `selfrepair_<x>` wrapper
// (redundancy + onchip_selfrepair): march-C controller + on-chip BIRA
// (onchip_row_repair_analyzer) + on-chip BISR sequencer (onchip_selfrepair_ctrl)
// + external row remap, around a spare-augmented memory. For hardening, the
// memory inside each wrapper is the sram_wrap_<x> adapter over the real OpenRAM
// macro; for the self-repair LOOP simulation, autombist's generated build swaps
// in sram_model_spares (with a forced defect) -- that inject->detect->repair->
// re-pass loop is covered per-memory by the Step E onchip tests.
//
//   slot 0 : selfrepair_a  32b x 256  (sky130_sram_32b256w)
//   slot 1 : selfrepair_b  32b x 512  (sky130_sram_32b512w)
//   slot 2 : selfrepair_c   8b x 1024 (sky130_sram_8b1024w)
//
// Functional access is the same muxed bus as the plain mem_subsystem (mem_sel
// picks the memory, read mux on a 1-cycle-delayed select). Self-repair is a
// single broadcast `self_repair_start`; the design reports aggregate
// done(all)/fail(any)/busy(any) plus a per-memory bist status vector.
// -----------------------------------------------------------------------------
module mem_subsystem_mbist (
    input  logic        clk,
    input  logic        rst_n,

    // functional bus
    input  logic        csb,
    input  logic        we,
    input  logic [1:0]  mem_sel,
    input  logic [9:0]  addr,       // widest logical addr (slot 1: 512 words)
    input  logic [31:0] wdata,
    output logic [31:0] rdata,

    // MBIST / self-repair control (broadcast) + aggregate status
    input  logic        test_mode,
    input  logic        bist_start,
    output logic        bist_done,      // all memories done
    output logic        bist_fail,      // any memory failed
    input  logic        self_repair_start,
    output logic        self_repair_done, // all done
    output logic        self_repair_fail, // any failed (unrepairable)
    output logic        self_repair_busy  // any busy
);
    // Per-slot functional chip-select (active-low), only the addressed slot on.
    wire csb0 = csb | (mem_sel != 2'd0);
    wire csb1 = csb | (mem_sel != 2'd1);
    wire csb2 = csb | (mem_sel != 2'd2);

    wire [31:0] d0, d1;
    wire [7:0]  d2;
    wire bd0, bd1, bd2, bf0, bf1, bf2;
    wire srd0, srd1, srd2, srf0, srf1, srf2, srb0, srb1, srb2;

    selfrepair_a u0 (
        .clk(clk), .rst_n(rst_n), .test_mode(test_mode),
        .bist_start(bist_start), .bist_done(bd0), .bist_fail(bf0),
        .func_csb(csb0), .func_addr(addr[7:0]), .func_din(wdata), .func_we(we), .func_dout(d0),
        .self_repair_start(self_repair_start),
        .self_repair_done(srd0), .self_repair_fail(srf0), .self_repair_busy(srb0)
    );
    selfrepair_b u1 (
        .clk(clk), .rst_n(rst_n), .test_mode(test_mode),
        .bist_start(bist_start), .bist_done(bd1), .bist_fail(bf1),
        .func_csb(csb1), .func_addr(addr[8:0]), .func_din(wdata), .func_we(we), .func_dout(d1),
        .self_repair_start(self_repair_start),
        .self_repair_done(srd1), .self_repair_fail(srf1), .self_repair_busy(srb1)
    );
    selfrepair_c u2 (
        .clk(clk), .rst_n(rst_n), .test_mode(test_mode),
        .bist_start(bist_start), .bist_done(bd2), .bist_fail(bf2),
        .func_csb(csb2), .func_addr(addr[9:0]), .func_din(wdata[7:0]), .func_we(we), .func_dout(d2),
        .self_repair_start(self_repair_start),
        .self_repair_done(srd2), .self_repair_fail(srf2), .self_repair_busy(srb2)
    );

    // Read mux, aligned to the memories' 1-cycle registered-read latency.
    logic [1:0] sel_q;
    always_ff @(posedge clk) sel_q <= mem_sel;
    always_comb begin
        case (sel_q)
            2'd0:    rdata = d0;
            2'd1:    rdata = d1;
            2'd2:    rdata = {24'b0, d2};
            default: rdata = '0;
        endcase
    end

    assign bist_done        = bd0 & bd1 & bd2;
    assign bist_fail        = bf0 | bf1 | bf2;
    assign self_repair_done = srd0 & srd1 & srd2;
    assign self_repair_fail = srf0 | srf1 | srf2;
    assign self_repair_busy = srb0 | srb1 | srb2;
endmodule
