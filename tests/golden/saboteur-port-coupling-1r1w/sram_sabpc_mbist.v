`timescale 1ns/1ps

module sram_sabpc_mbist #(
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

    logic mbist_busy;

    logic                  mbist_sram_clk0;
    logic                  mbist_sram_csb0;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr0;
    logic [DATA_WIDTH-1:0] sram_dout0;
    logic [ADDR_WIDTH-1:0] sram_addr0;
    logic                  sram_csb0;
    logic                  mbist_sram_clk1;
    logic                  mbist_sram_csb1;
    logic                  mbist_sram_web1;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr1;
    logic [DATA_WIDTH-1:0] mbist_sram_din1;
    logic [ADDR_WIDTH-1:0] sram_addr1;
    logic [DATA_WIDTH-1:0] sram_din1;
    logic                  sram_we1;
    logic                  selected_write_req1;
    logic                  mbist_write_req1;
    logic                  sram_csb1;

    logic [DATA_WIDTH-1:0] sram_dbg_actual_word;
    logic [DATA_WIDTH-1:0] sram_dbg_fault_word;
    logic [DATA_WIDTH-1:0] sram_dbg_intended_word;
    logic [DATA_WIDTH-1:0] sram_dbg_prior_value;
    logic [DATA_WIDTH-1:0] sram_dbg_attempted_write;
    logic [DATA_WIDTH-1:0] sram_dbg_actual_read;
    logic [DATA_WIDTH-1:0] sram_dbg_blocked_bits;

    march_1r1w_top #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(1)
    ) u_algo_top (
        .clk(clk),
        .rst_n(rst_n),
        .bist_start(bist_start && test_mode),
        .bist_busy(mbist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .sram_clk0(mbist_sram_clk0),
        .sram_csb0(mbist_sram_csb0),
        .sram_addr0(mbist_sram_addr0),
        .sram_dout0(sram_dout0),
        .sram_clk1(mbist_sram_clk1),
        .sram_csb1(mbist_sram_csb1),
        .sram_web1(mbist_sram_web1),
        .sram_addr1(mbist_sram_addr1),
        .sram_din1(mbist_sram_din1)
    );

    assign sram_addr0 = test_mode ? mbist_sram_addr0 : func_addr;
    assign sram_csb0  = test_mode ? mbist_sram_csb0 : func_csb;
    assign func_dout = sram_dout0;
    assign mbist_write_req1    = ~mbist_sram_web1;
    assign selected_write_req1 = test_mode ? mbist_write_req1 : func_we;

    assign sram_we1 = ~selected_write_req1;

    assign sram_addr1 = test_mode ? mbist_sram_addr1 : func_addr;
    assign sram_din1  = test_mode ? mbist_sram_din1 : func_din;
    assign sram_csb1  = test_mode ? mbist_sram_csb1 : func_csb;

    sram_sabpc_saboteur #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_sram (
        .clk0(mbist_sram_clk0),
        .csb0(sram_csb0),
        .addr0(sram_addr0),
        .dout0(sram_dout0),
        .clk1(mbist_sram_clk1),
        .csb1(sram_csb1),
        .web1(sram_we1),
        .addr1(sram_addr1),
        .din1(sram_din1),
        .dbg_actual_word(sram_dbg_actual_word),
        .dbg_fault_word(sram_dbg_fault_word),
        .dbg_intended_word(sram_dbg_intended_word),
        .dbg_prior_value(sram_dbg_prior_value),
        .dbg_attempted_write(sram_dbg_attempted_write),
        .dbg_actual_read(sram_dbg_actual_read),
        .dbg_blocked_bits(sram_dbg_blocked_bits)
    );

endmodule
