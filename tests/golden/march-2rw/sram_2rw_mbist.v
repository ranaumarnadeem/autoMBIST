`timescale 1ns/1ps

module sram_2rw_mbist #(
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
    logic                  mbist_sram_web0;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr0;
    logic [DATA_WIDTH-1:0] mbist_sram_din0;
    logic [DATA_WIDTH-1:0] sram_dout0;
    logic [ADDR_WIDTH-1:0] sram_addr0;
    logic [DATA_WIDTH-1:0] sram_din0;
    logic                  sram_we0;
    logic                  selected_write_req0;
    logic                  mbist_write_req0;
    logic                  sram_csb0;
    logic                  mbist_sram_clk1;
    logic                  mbist_sram_csb1;
    logic                  mbist_sram_web1;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr1;
    logic [DATA_WIDTH-1:0] mbist_sram_din1;
    logic [DATA_WIDTH-1:0] sram_dout1;
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

    march_2rw_top #(
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
        .sram_web0(mbist_sram_web0),
        .sram_addr0(mbist_sram_addr0),
        .sram_din0(mbist_sram_din0),
        .sram_dout0(sram_dout0),
        .sram_clk1(mbist_sram_clk1),
        .sram_csb1(mbist_sram_csb1),
        .sram_web1(mbist_sram_web1),
        .sram_addr1(mbist_sram_addr1),
        .sram_din1(mbist_sram_din1),
        .sram_dout1(sram_dout1)
    );

    assign mbist_write_req0    = ~mbist_sram_web0;
    assign selected_write_req0 = test_mode ? mbist_write_req0 : func_we;

    assign sram_we0 = ~selected_write_req0;

    assign sram_addr0 = test_mode ? mbist_sram_addr0 : func_addr;
    assign sram_din0  = test_mode ? mbist_sram_din0 : func_din;
    assign sram_csb0  = test_mode ? mbist_sram_csb0 : func_csb;
    // march-2rw's functional boundary is inherently single-port: only the
    // port assigned suffix "0" drives func_dout in functional (test_mode=0)
    // mode. The second port exists for the MBIST algorithm's internal
    // concurrency (e.g. concurrent read+write to different/same addresses),
    // not for external dual-port functional access.
    assign func_dout = sram_dout0;
    assign mbist_write_req1    = ~mbist_sram_web1;
    assign selected_write_req1 = test_mode ? mbist_write_req1 : func_we;

    assign sram_we1 = ~selected_write_req1;

    assign sram_addr1 = test_mode ? mbist_sram_addr1 : func_addr;
    assign sram_din1  = test_mode ? mbist_sram_din1 : func_din;
    assign sram_csb1  = test_mode ? mbist_sram_csb1 : func_csb;

    sram_2rw #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_sram (
        .clk0(mbist_sram_clk0),
        .csb0(sram_csb0),
        .web0(sram_we0),
        .addr0(sram_addr0),
        .din0(sram_din0),
        .dout0(sram_dout0),
        .clk1(mbist_sram_clk1),
        .csb1(sram_csb1),
        .web1(sram_we1),
        .addr1(sram_addr1),
        .din1(sram_din1),
        .dout1(sram_dout1)
    );

endmodule
