`timescale 1ns/1ps

module sram_tcol_mbist #(
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
  , input logic [1-1:0] row_repair_en
  , input logic [4-1:0] faulty_row_addr
  , input logic [1-1:0] col_repair_en
  , input logic [3-1:0] faulty_bit
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
    logic [DATA_WIDTH-1:0] sram_dbg_actual_word;
    logic [DATA_WIDTH-1:0] sram_dbg_fault_word;
    logic [DATA_WIDTH-1:0] sram_dbg_intended_word;
    logic [DATA_WIDTH-1:0] sram_dbg_prior_value;
    logic [DATA_WIDTH-1:0] sram_dbg_attempted_write;
    logic [DATA_WIDTH-1:0] sram_dbg_actual_read;
    logic [DATA_WIDTH-1:0] sram_dbg_blocked_bits;
    logic                  sram_csb;
    logic                  sram_we;

    logic selected_write_req;
    logic mbist_write_req;


    march_c_top #(
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

    // Row redundancy: an EXTERNAL repair remap sits between the address mux and a
    // stock spare-augmented memory. The repair_ports pins drive the remap (not the
    // memory, which has no repair pins); the memory just sees the remapped address.
    logic [5-1:0] sram_addr_phys;

    repair_remap_row #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_SPARE_ROWS(1)
    ) u_repair_remap (
        .addr_in(sram_addr),
        .addr_out(sram_addr_phys)
      , .row_repair_en(row_repair_en)
      , .faulty_row_addr(faulty_row_addr)
    );

    // Column redundancy: a second EXTERNAL remap on the DATA path, steering the
    // bits marked faulty onto spare columns. Independent of the row remap above
    // (that one is address-only, this one is data-only), so the two compose:
    // a row-steered access still gets its column steer, on the spare row's own
    // spare columns. sram_dout is driven from HERE, not from the memory -- the
    // memory drives sram_dout_phys instead.
    //
    // These widths are generator-time CONSTANTS, baked from the config's
    // data_width -- they do not track an ADDR_WIDTH/DATA_WIDTH override at
    // instantiation, and neither does the faulty_bit boundary port (whose width
    // comes from the repair_ports entry). That matches sram_addr_phys above and
    // the memory instantiation itself: a generated wrapper is built FOR one
    // memory geometry, and its parameters document that geometry rather than
    // resize it. Overriding them at instantiation was never supported.
    logic [9-1:0] sram_din_phys;
    logic [9-1:0] sram_dout_phys;
    logic [1-1:0] sram_spare_wen;

    repair_remap_col #(
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_COLS(1)
    ) u_repair_remap_col (
        .din_in(sram_din),
        .din_out(sram_din_phys),
        .dout_in(sram_dout_phys),
        .dout_out(sram_dout),
        .spare_wen(sram_spare_wen)
      , .col_repair_en(col_repair_en)
      , .faulty_bit(faulty_bit)
    );

    sram_tcol #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_ROWS(1),
        .NUM_SPARE_COLS(1)
    ) u_sram (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb),
        .addr0(sram_addr_phys),
        .din0(sram_din_phys),
        .web0(sram_we),
        .dout0(sram_dout_phys),
        .spare_wen0(sram_spare_wen)
    );

endmodule
