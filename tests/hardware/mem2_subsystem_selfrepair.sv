`timescale 1ns/1ps
// -----------------------------------------------------------------------------
// mem2_subsystem_selfrepair: a small 2-memory integration test analogous to
// flow/multimem/mbist/mem_subsystem_mbist.sv, but built for FUNCTIONAL
// simulation of autonomous on-chip self-repair running on multiple memories
// TOGETHER in one design -- something the real (hardening-only) subsystem's
// generated wrappers can't do, since those instantiate the real macro adapters
// (no defect-injection knob). Here each slot's generated self-repair wrapper
// instantiates a defect-injectable sram_spares_intmem_{a,b}.v fixture instead.
//
//   slot 0 : selfrepair_intmem_a  4-bit addr (16 words) x 8-bit data, 1 spare row,
//            baked-in defect at (addr=5, bit=2), stuck-at-1
//   slot 1 : selfrepair_intmem_b  5-bit addr (32 words) x 8-bit data, 1 spare row,
//            baked-in defect at (addr=20, bit=6), stuck-at-0 -- deliberately a
//            DIFFERENT address/bit/polarity than slot 0, so a passing test
//            proves independent per-memory repair, not two identical repairs
//            that happen to look the same.
//
// Functional access is muxed by mem_sel (same 1-cycle registered-read-latency
// pattern as the real subsystem). Self-repair is a single broadcast
// self_repair_start; aggregate done(all)/fail(any)/busy(any) are reported,
// but the test verifies each memory INDEPENDENTLY via its own functional port
// -- never trusting the aggregate status alone.
// -----------------------------------------------------------------------------
module mem2_subsystem_selfrepair (
    input  logic       clk,
    input  logic       rst_n,

    // functional bus
    input  logic       csb,
    input  logic       we,
    input  logic       mem_sel,        // 0 = slot A, 1 = slot B
    input  logic [4:0] addr,           // widest logical addr (slot B: 5 bits)
    input  logic [7:0] wdata,
    output logic [7:0] rdata,

    // MBIST / self-repair control (broadcast) + aggregate status
    input  logic       test_mode,
    input  logic       bist_start,
    output logic       bist_done,       // all memories done
    output logic       bist_fail,       // any memory failed
    input  logic       self_repair_start,
    output logic       self_repair_done, // all done
    output logic       self_repair_fail, // any failed (unrepairable)
    output logic       self_repair_busy, // any busy

    // per-memory functional access, for INDEPENDENT post-repair verification
    // (bypasses mem_sel muxing so the test can scan one memory at a time
    // without needing to also worry about the other slot's csb gating)
    output logic [7:0] func_dout_a,
    output logic [7:0] func_dout_b
);
    wire csb_a = csb | mem_sel;
    wire csb_b = csb | ~mem_sel;

    wire [7:0] da, db;
    wire bda, bdb, bfa, bfb;
    wire srda, srdb, srfa, srfb, srba, srbb;

    selfrepair_intmem_a u_a (
        .clk(clk), .rst_n(rst_n), .test_mode(test_mode),
        .bist_start(bist_start), .bist_done(bda), .bist_fail(bfa),
        .func_csb(csb_a), .func_addr(addr[3:0]), .func_din(wdata), .func_we(we), .func_dout(da),
        .self_repair_start(self_repair_start),
        .self_repair_done(srda), .self_repair_fail(srfa), .self_repair_busy(srba)
    );
    selfrepair_intmem_b u_b (
        .clk(clk), .rst_n(rst_n), .test_mode(test_mode),
        .bist_start(bist_start), .bist_done(bdb), .bist_fail(bfb),
        .func_csb(csb_b), .func_addr(addr[4:0]), .func_din(wdata), .func_we(we), .func_dout(db),
        .self_repair_start(self_repair_start),
        .self_repair_done(srdb), .self_repair_fail(srfb), .self_repair_busy(srbb)
    );

    logic sel_q;
    always_ff @(posedge clk) sel_q <= mem_sel;
    always_comb rdata = sel_q ? db : da;

    assign func_dout_a = da;
    assign func_dout_b = db;

    assign bist_done        = bda & bdb;
    assign bist_fail        = bfa | bfb;
    assign self_repair_done = srda & srdb;
    assign self_repair_fail = srfa | srfb;
    assign self_repair_busy = srba | srbb;
endmodule
