`timescale 1ns/1ps

module sram_spd_mbist #(
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
  , input  logic self_repair_start
  , output logic self_repair_done
  , output logic self_repair_fail
  , output logic self_repair_busy
  , input  logic repair_load
  , input  logic [2-1:0] fuse_row_repair_en
  , input  logic [2*ADDR_WIDTH-1:0] fuse_faulty_row_addr
  , output logic repair_load_done
  , output logic [4-1:0] diag_valid
  , output logic [4*ADDR_WIDTH-1:0] diag_addr
  , output logic diag_overflow
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

    // Declared here (not alongside their instantiations below) because Icarus
    // requires a wire's declaration to textually precede its use, and these
    // are read by u_algo_top's instantiation and the test_mode mux immediately
    // below -- both ahead of where the modules that drive them are instantiated.
    logic                  algo_fail_valid;
    logic [ADDR_WIDTH-1:0] algo_fail_addr;
    logic                  effective_test_mode;
    logic                  effective_bist_start;
    logic                  ctrl_test_mode_override;
    logic                  ctrl_bist_start;
    logic                  registrar_enable;
    logic                  latch_result;
    logic                  unrepairable;
    logic [2-1:0]            row_repair_en;
    logic [2*ADDR_WIDTH-1:0] faulty_row_addr;

    assign effective_test_mode  = test_mode || ctrl_test_mode_override;
    assign effective_bist_start = (bist_start && test_mode && !self_repair_busy) || ctrl_bist_start;

    march_c_top #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(1)
    ) u_algo_top (
        .clk(clk),
        .rst_n(rst_n),
        .bist_start(effective_bist_start),
        .bist_busy(mbist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .bist_fail_valid(algo_fail_valid),
        .bist_fail_addr(algo_fail_addr),
        .sram_clk0(mbist_sram_clk),
        .sram_csb0(mbist_sram_csb),
        .sram_web0(mbist_sram_web),
        .sram_addr0(mbist_sram_addr),
        .sram_din0(mbist_sram_din),
        .sram_dout0(sram_dout)
    );

    assign mbist_write_req    = ~mbist_sram_web;
    assign selected_write_req = effective_test_mode ? mbist_write_req : func_we;

    assign sram_we = ~selected_write_req;

    assign sram_addr = effective_test_mode ? mbist_sram_addr : func_addr;
    assign sram_din  = effective_test_mode ? mbist_sram_din : func_din;
    assign sram_csb  = effective_test_mode ? mbist_sram_csb : func_csb;
    assign func_dout = sram_dout;

    // Row redundancy (on-chip self-repair): an EXTERNAL repair remap sits between
    // the address mux and a stock spare-augmented memory. An on-chip analyzer +
    // sequencer compute and apply the repair signature autonomously -- no
    // repair_ports/tester involvement (mutually exclusive by config validation).
    // (algo_fail_valid/algo_fail_addr/effective_test_mode/effective_bist_start/
    // row_repair_en/faulty_row_addr etc. are declared earlier, alongside
    // mbist_busy -- Icarus needs the declaration to precede u_algo_top's
    // instantiation and the test_mode mux, both of which come before this.)
    logic [5-1:0] sram_addr_phys;

    onchip_row_repair_analyzer #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_SPARE_ROWS(2)
    ) u_onchip_analyzer (
        .clk(clk),
        .rst_n(rst_n),
        .enable(registrar_enable),
        .fail_valid(algo_fail_valid),
        .fail_addr(algo_fail_addr),
        .latch_result(latch_result),
        .repair_load(repair_load),
        .fuse_row_repair_en(fuse_row_repair_en),
        .fuse_faulty_row_addr(fuse_faulty_row_addr),
        .repair_load_done(repair_load_done),
        .row_repair_en(row_repair_en),
        .faulty_row_addr(faulty_row_addr),
        .unrepairable(unrepairable),
        .verify_enable(1'b0),
        .retire(1'b0),
        .has_suspect()
    );

    onchip_diagnosis_log #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_DIAGNOSIS_ENTRIES(4)
    ) u_onchip_diagnosis (
        .clk(clk),
        .rst_n(rst_n),
        .enable(registrar_enable),
        .fail_valid(algo_fail_valid),
        .fail_addr(algo_fail_addr),
        .latch_result(latch_result),
        .diag_valid(diag_valid),
        .diag_addr(diag_addr),
        .diag_overflow(diag_overflow)
    );

    onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl (
        .clk(clk),
        .rst_n(rst_n),
        .self_repair_start(self_repair_start),
        .self_repair_done(self_repair_done),
        .self_repair_fail(self_repair_fail),
        .self_repair_busy(self_repair_busy),
        .ctrl_test_mode_override(ctrl_test_mode_override),
        .ctrl_bist_start(ctrl_bist_start),
        .mbist_busy(mbist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .registrar_enable(registrar_enable),
        .latch_result(latch_result),
        .unrepairable(unrepairable),
        .verify_enable(),
        .retire(),
        .diag_enable(),
        .has_suspect(1'b0),
        .defer()
    );

    repair_remap_row #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_SPARE_ROWS(2)
    ) u_repair_remap (
        .addr_in(sram_addr),
        .addr_out(sram_addr_phys)
      , .row_repair_en(row_repair_en)
      , .faulty_row_addr(faulty_row_addr)
    );

    sram_spd #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_ROWS(2)
    ) u_sram (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb),
        .addr0(sram_addr_phys),
        .din0(sram_din),
        .web0(sram_we),
        .dout0(sram_dout)
    );

endmodule
