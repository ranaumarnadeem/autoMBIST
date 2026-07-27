`timescale 1ns/1ps

// Thin wrapper matching march_c_top.sv's style, exposing OpenRAM-style pins
// for BOTH ports of a 1-read-port + 1-write-port memory. This signal shape
// intentionally lines up with the Phase 1 config schema port types already
// landed in generator.py: "r" (clk/addr/dout/csb) and "w"
// (clk/addr/din/csb/we), so a later phase can wire this mechanically.
module march_1r1w_top #(
    parameter integer ADDR_WIDTH   = 10,
    parameter integer DATA_WIDTH   = 32,
    parameter integer READ_LATENCY = 1
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  bist_start,

    output logic                  bist_busy,
    output logic                  bist_done,
    output logic                  bist_fail,

    // On-chip BIRA streaming interface (Step E) -- pass-through of the FSM's
    // own fail_valid/fail_addr. Unconnected/unused by every existing consumer.
    output logic                  bist_fail_valid,
    output logic [ADDR_WIDTH-1:0] bist_fail_addr,

    // Port 0: read-only (structurally cannot write -- no web0/din0).
    output logic                  sram_clk0,
    output logic                  sram_csb0,
    output logic [ADDR_WIDTH-1:0] sram_addr0,
    input  logic [DATA_WIDTH-1:0] sram_dout0,

    // Port 1: write-only (structurally cannot read -- no dout1).
    output logic                  sram_clk1,
    output logic                  sram_csb1,
    output logic                  sram_web1,
    output logic [ADDR_WIDTH-1:0] sram_addr1,
    output logic [DATA_WIDTH-1:0] sram_din1
);

    logic                  mem_en0;
    logic [ADDR_WIDTH-1:0] mem_addr0;

    logic                  mem_en1;
    logic [ADDR_WIDTH-1:0] mem_addr1;
    logic [DATA_WIDTH-1:0] mem_wdata1;

    march_1r1w_fsm #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(READ_LATENCY)
    ) u_march_1r1w_fsm (
        .clk(clk),
        .rst_n(rst_n),
        .start(bist_start),
        .mem_rdata0(sram_dout0),
        .mem_en0(mem_en0),
        .mem_addr0(mem_addr0),
        .mem_en1(mem_en1),
        .mem_addr1(mem_addr1),
        .mem_wdata1(mem_wdata1),
        .busy(bist_busy),
        .done(bist_done),
        .fail(bist_fail),
        .fail_valid(bist_fail_valid),
        .fail_addr(bist_fail_addr)
    );

    // Keep OpenRAM-style naming and active-low polarity at the boundary.
    assign sram_clk0  = clk;
    assign sram_csb0  = ~mem_en0;
    assign sram_addr0 = mem_addr0;

    assign sram_clk1  = clk;
    assign sram_csb1  = ~mem_en1;
    assign sram_web1  = ~mem_en1;
    assign sram_addr1 = mem_addr1;
    assign sram_din1  = mem_wdata1;

endmodule
