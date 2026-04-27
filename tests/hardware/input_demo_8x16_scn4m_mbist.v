`timescale 1ns/1ps

module input_demo_8x16_scn4m_mbist #(
    parameter integer ADDR_WIDTH = 4,
    parameter integer DATA_WIDTH = 8
) (
    input  logic                  clk,
    input  logic                  rst_n,

    input  logic                  test_mode,
    input  logic                  bist_start,
    output logic                  bist_done,
    output logic                  bist_fail,

    input  logic                  func_csb,
    input  logic [ADDR_WIDTH-1:0] func_addr,
    input  logic [DATA_WIDTH-1:0] func_din,
    input  logic                  func_we,
    output logic [DATA_WIDTH-1:0] func_dout
);

    logic                  mbist_busy;
    logic                  mbist_sram_clk;
    logic                  mbist_sram_csb;
    logic                  mbist_sram_web;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr;
    logic [DATA_WIDTH-1:0] mbist_sram_din;

    logic [ADDR_WIDTH-1:0] sram_addr;
    logic [DATA_WIDTH-1:0] sram_din;
    logic [DATA_WIDTH-1:0] sram_dout;
    logic                  sram_csb;
    logic                  sram_we;

    logic selected_write_req;
    logic mbist_write_req;

    mbist_top #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(1)
    ) u_mbist_top (
        .clk(clk),
        .rst_n(rst_n),
        .bist_start(bist_start && test_mode),
        .bist_busy(mbist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .sram_clk0(mbist_sram_clk),
        .sram_csb0(mbist_sram_csb),
        .sram_web0(mbist_sram_web),
        .sram_addr0(mbist_sram_addr),
        .sram_din0(mbist_sram_din),
        .sram_dout0(sram_dout)
    );

    assign mbist_write_req    = ~mbist_sram_web;
    assign selected_write_req = test_mode ? mbist_write_req : func_we;

    assign sram_we = ~selected_write_req;

    assign sram_addr = test_mode ? mbist_sram_addr : func_addr;
    assign sram_din  = test_mode ? mbist_sram_din : func_din;
    assign sram_csb  = test_mode ? mbist_sram_csb : func_csb;
    assign func_dout = sram_dout;

    input_demo_8x16_scn4m_saboteur #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_sram (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb),
        .addr0(sram_addr),
        .din0(sram_din),
        .web0(sram_we),
        .dout0(sram_dout)
    );

endmodule
