`timescale 1ns/1ps

module mbist_top #(
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

    output logic                  sram_clk0,
    output logic                  sram_csb0,
    output logic                  sram_web0,
    output logic [ADDR_WIDTH-1:0] sram_addr0,
    output logic [DATA_WIDTH-1:0] sram_din0,
    input  logic [DATA_WIDTH-1:0] sram_dout0
);

    logic                  mem_en;
    logic                  mem_we;
    logic [ADDR_WIDTH-1:0] mem_addr;
    logic [DATA_WIDTH-1:0] mem_wdata;

    mbist_fsm #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(READ_LATENCY)
    ) u_mbist_fsm (
        .clk(clk),
        .rst_n(rst_n),
        .start(bist_start),
        .mem_rdata(sram_dout0),
        .mem_en(mem_en),
        .mem_we(mem_we),
        .mem_addr(mem_addr),
        .mem_wdata(mem_wdata),
        .busy(bist_busy),
        .done(bist_done),
        .fail(bist_fail)
    );

    // Keep OpenRAM-style naming and active-low polarity at the boundary.
    assign sram_clk0  = clk;
    assign sram_csb0  = ~mem_en;
    assign sram_web0  = ~mem_we;
    assign sram_addr0 = mem_addr;
    assign sram_din0  = mem_wdata;

endmodule
