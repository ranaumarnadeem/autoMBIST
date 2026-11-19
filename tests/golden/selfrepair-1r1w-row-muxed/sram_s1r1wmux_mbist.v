`timescale 1ns/1ps

module sram_s1r1wmux_mbist #(
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
);

    logic mbist_busy;

    // On-chip self-repair (march-1r1w): declared here (not alongside their
    // instantiations below) because Icarus requires a wire's declaration to
    // textually precede its use, and these are read by u_algo_top's
    // instantiation and the effective_test_mode muxes immediately below -- both
    // ahead of where the modules that drive them are instantiated.
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

    logic                  mbist_sram_clk0;
    logic                  mbist_sram_csb0;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr0;
    logic [DATA_WIDTH-1:0] sram_dout0;
    logic [ADDR_WIDTH-1:0] sram_addr0;
    logic [5-1:0] sram_addr_phys0;
    logic                  sram_csb0;
    logic                  mbist_sram_clk1;
    logic                  mbist_sram_csb1;
    logic                  mbist_sram_web1;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr1;
    logic [DATA_WIDTH-1:0] mbist_sram_din1;
    logic [ADDR_WIDTH-1:0] sram_addr1;
    logic [5-1:0] sram_addr_phys1;
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
        .bist_start(effective_bist_start),
        .bist_busy(mbist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .bist_fail_valid(algo_fail_valid),
        .bist_fail_addr(algo_fail_addr),
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

    assign sram_addr0 = effective_test_mode ? mbist_sram_addr0 : func_addr;
    assign sram_csb0  = effective_test_mode ? mbist_sram_csb0 : func_csb;
    assign func_dout = sram_dout0;
    assign mbist_write_req1    = ~mbist_sram_web1;
    assign selected_write_req1 = effective_test_mode ? mbist_write_req1 : func_we;

    assign sram_we1 = ~selected_write_req1;

    assign sram_addr1 = effective_test_mode ? mbist_sram_addr1 : func_addr;
    assign sram_din1  = effective_test_mode ? mbist_sram_din1 : func_din;
    assign sram_csb1  = effective_test_mode ? mbist_sram_csb1 : func_csb;

    // On-chip self-repair: one external repair remap PER PORT, each steering
    // that port's own address independently. Neither two-port algorithm keeps
    // its ports on one address: march-1r1w writes port 1 one address behind
    // port 0's read, and march-2rw does the same and also uses
    // addr_q ^ PARTNER_XOR under use_partner_addr1 -- collapsing both onto
    // one remap fed by only one port's address would silently discard the
    // other port's real address the moment self-repair is enabled, corrupting
    // normal dual-port addressing, not just leaving repair redirection
    // incomplete. repair_remap_row is purely combinational and deterministic,
    // so every instance sees the SAME row_repair_en/faulty_row_addr from the
    // one analyzer below and remaps consistently.
    onchip_row_repair_analyzer #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_SPARE_ROWS(2)
      , .WORDS_PER_ROW(2)
    ) u_onchip_analyzer (
        .clk(clk),
        .rst_n(rst_n),
        .enable(registrar_enable),
        .fail_valid(algo_fail_valid),
        .fail_addr(algo_fail_addr),
        .latch_result(latch_result),
        .repair_load(1'b0),
        .fuse_row_repair_en('0),
        .fuse_faulty_row_addr('0),
        .repair_load_done(),
        .row_repair_en(row_repair_en),
        .faulty_row_addr(faulty_row_addr),
        .unrepairable(unrepairable),
        .verify_enable(1'b0),
        .retire(1'b0),
        .has_suspect()
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
      , .WORDS_PER_ROW(2)
    ) u_repair_remap0 (
        .addr_in(sram_addr0)
      , .addr_out(sram_addr_phys0)
      , .row_repair_en(row_repair_en)
      , .faulty_row_addr(faulty_row_addr)
    );
    repair_remap_row #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_SPARE_ROWS(2)
      , .WORDS_PER_ROW(2)
    ) u_repair_remap1 (
        .addr_in(sram_addr1)
      , .addr_out(sram_addr_phys1)
      , .row_repair_en(row_repair_en)
      , .faulty_row_addr(faulty_row_addr)
    );

    sram_s1r1wmux #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
      , .NUM_SPARE_ROWS(2)
    ) u_sram (
        .clk0(mbist_sram_clk0),
        .csb0(sram_csb0),
        .addr0(sram_addr_phys0),
        .dout0(sram_dout0),
        .clk1(mbist_sram_clk1),
        .csb1(sram_csb1),
        .web1(sram_we1),
        .addr1(sram_addr_phys1),
        .din1(sram_din1)
    );

endmodule
