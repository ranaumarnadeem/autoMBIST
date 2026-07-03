`timescale 1ns/1ps

// Thin wrapper matching march_1r1w_top.sv's style, exposing OpenRAM-style
// pins for BOTH ports of a 2-read-write-port memory. Unlike
// march_1r1w_top.sv (port 0 read-only, port 1 write-only), both ports here
// expose the full read/write pin set (web/din/dout on both), matching a
// genuinely symmetric 2RW SRAM macro.
module march_2rw_top #(
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

    // Port 0: full read/write.
    output logic                  sram_clk0,
    output logic                  sram_csb0,
    output logic                  sram_web0,
    output logic [ADDR_WIDTH-1:0] sram_addr0,
    output logic [DATA_WIDTH-1:0] sram_din0,
    input  logic [DATA_WIDTH-1:0] sram_dout0,

    // Port 1: full read/write.
    output logic                  sram_clk1,
    output logic                  sram_csb1,
    output logic                  sram_web1,
    output logic [ADDR_WIDTH-1:0] sram_addr1,
    output logic [DATA_WIDTH-1:0] sram_din1,
    input  logic [DATA_WIDTH-1:0] sram_dout1
);

    logic                  mem_en0;
    logic                  mem_we0;
    logic [ADDR_WIDTH-1:0] mem_addr0;
    logic [DATA_WIDTH-1:0] mem_wdata0;

    logic                  mem_en1;
    logic                  mem_we1;
    logic [ADDR_WIDTH-1:0] mem_addr1;
    logic [DATA_WIDTH-1:0] mem_wdata1;

    march_2rw_fsm #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(READ_LATENCY)
    ) u_march_2rw_fsm (
        .clk(clk),
        .rst_n(rst_n),
        .start(bist_start),
        .mem_rdata0(sram_dout0),
        .mem_rdata1(sram_dout1),
        .mem_en0(mem_en0),
        .mem_we0(mem_we0),
        .mem_addr0(mem_addr0),
        .mem_wdata0(mem_wdata0),
        .mem_en1(mem_en1),
        .mem_we1(mem_we1),
        .mem_addr1(mem_addr1),
        .mem_wdata1(mem_wdata1),
        .busy(bist_busy),
        .done(bist_done),
        .fail(bist_fail)
    );

    // Keep OpenRAM-style naming and active-low polarity at the boundary.
    assign sram_clk0  = clk;
    assign sram_csb0  = ~mem_en0;
    assign sram_web0  = ~mem_we0;
    assign sram_addr0 = mem_addr0;
    assign sram_din0  = mem_wdata0;

    assign sram_clk1  = clk;
    assign sram_csb1  = ~mem_en1;
    assign sram_web1  = ~mem_we1;
    assign sram_addr1 = mem_addr1;
    assign sram_din1  = mem_wdata1;

endmodule
