`timescale 1ns/1ps

module sram_s2rw_mbist #(
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
    logic                  verify_enable;
    logic                  retire;
    logic                  has_suspect;
    logic                  diag_enable;
    logic [2-1:0]            row_repair_en;
    logic [2*ADDR_WIDTH-1:0] faulty_row_addr;
    logic [DATA_WIDTH-1:0]                                  algo_fail_bitmask;
    logic [1-1:0]              col_repair_en;
    logic [1*3-1:0] faulty_bit;
    // TWO independent spare_wen wires, one per port: march-2rw's ports are
    // both fully read/write and independently commit their own writes (see
    // the per-port repair_remap_col instances below), so there is no single
    // shared spare_wen source the way the r+w (march-1r1w) shape has.
    logic [1-1:0]              sram_spare_wen0;
    logic [1-1:0]              sram_spare_wen1;

    assign effective_test_mode  = test_mode || ctrl_test_mode_override;
    assign effective_bist_start = (bist_start && test_mode && !self_repair_busy) || ctrl_bist_start;

    logic                  mbist_sram_clk0;
    logic                  mbist_sram_csb0;
    logic                  mbist_sram_web0;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr0;
    logic [DATA_WIDTH-1:0] mbist_sram_din0;
    logic [DATA_WIDTH-1:0] sram_dout0;
    logic [9-1:0] sram_dout_phys0;
    logic [ADDR_WIDTH-1:0] sram_addr0;
    logic [5-1:0] sram_addr_phys0;
    logic [DATA_WIDTH-1:0] sram_din0;
    logic [9-1:0] sram_din_phys0;
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
    logic [9-1:0] sram_dout_phys1;
    logic [ADDR_WIDTH-1:0] sram_addr1;
    logic [5-1:0] sram_addr_phys1;
    logic [DATA_WIDTH-1:0] sram_din1;
    logic [9-1:0] sram_din_phys1;
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
        .bist_start(effective_bist_start),
        .bist_busy(mbist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .bist_fail_valid(algo_fail_valid),
        .bist_fail_addr(algo_fail_addr),
        .bist_fail_bitmask(algo_fail_bitmask),
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
    assign selected_write_req0 = effective_test_mode ? mbist_write_req0 : func_we;

    assign sram_we0 = ~selected_write_req0;

    assign sram_addr0 = effective_test_mode ? mbist_sram_addr0 : func_addr;
    assign sram_din0  = effective_test_mode ? mbist_sram_din0 : func_din;
    assign sram_csb0  = effective_test_mode ? mbist_sram_csb0 : func_csb;
    // march-2rw's functional boundary is inherently single-port: only the
    // port assigned suffix "0" drives func_dout in functional (test_mode=0)
    // mode. The second port exists for the MBIST algorithm's internal
    // concurrency (e.g. concurrent read+write to different/same addresses),
    // not for external dual-port functional access.
    assign func_dout = sram_dout0;
    assign mbist_write_req1    = ~mbist_sram_web1;
    assign selected_write_req1 = effective_test_mode ? mbist_write_req1 : func_we;

    assign sram_we1 = ~selected_write_req1;

    assign sram_addr1 = effective_test_mode ? mbist_sram_addr1 : func_addr;
    assign sram_din1  = effective_test_mode ? mbist_sram_din1 : func_din;
    assign sram_csb1  = effective_test_mode ? mbist_sram_csb1 : func_csb;

    // On-chip self-repair: one external repair remap PER PORT, each steering
    // that port's own address independently. This is general on purpose, not
    // special-cased per algo -- march-1r1w's two ports happen to always carry
    // the identical logical address (one shared addr_q, no partner-address
    // concept anywhere in march_1r1w_fsm.sv), so its second remap instance is
    // a provably harmless duplicate, not a structural requirement. march-2rw's
    // ports do NOT share an address in general (addr_q vs.
    // addr_q ^ PARTNER_XOR under use_partner_addr1) -- collapsing both onto
    // one remap fed by only one port's address would silently discard the
    // other port's real address the moment self-repair is enabled, corrupting
    // normal dual-port addressing, not just leaving repair redirection
    // incomplete. repair_remap_row is purely combinational and deterministic,
    // so every instance sees the SAME row_repair_en/faulty_row_addr from the
    // one analyzer below and remaps consistently.
    // Column repair, unlike the row remap above, is NOT per-port:
    // col_repair_en/faulty_bit are a single set of claims fed into one
    // repair_remap_col instance below, wired across whichever port
    // physically reads and whichever physically writes.
    onchip_2d_repair_analyzer #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_ROWS(2),
        .NUM_SPARE_COLS(1)
    ) u_onchip_analyzer (
        .clk(clk),
        .rst_n(rst_n),
        .enable(registrar_enable),
        .fail_valid(algo_fail_valid),
        .fail_addr(algo_fail_addr),
        .fail_bitmask(algo_fail_bitmask),
        .latch_result(latch_result),
        .repair_load(1'b0),
        .fuse_row_repair_en('0),
        .fuse_faulty_row_addr('0),
        .repair_load_done(),
        .row_repair_en(row_repair_en),
        .faulty_row_addr(faulty_row_addr),
        .col_repair_en(col_repair_en),
        .faulty_bit(faulty_bit),
        .unrepairable(unrepairable),
        .verify_enable(verify_enable),
        .retire(retire),
        .has_suspect(has_suspect),
        .defer(1'b0)
    );

    onchip_selfrepair_ctrl #(
        .MAX_RETRIES(3),
        .TWO_PASS(0)
    ) u_onchip_selfrepair_ctrl (
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
        .verify_enable(verify_enable),
        .retire(retire),
        .diag_enable(diag_enable),
        .has_suspect(has_suspect),
        .defer()
    );

    repair_remap_row #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_SPARE_ROWS(2)
    ) u_repair_remap0 (
        .addr_in(sram_addr0)
      , .addr_out(sram_addr_phys0)
      , .row_repair_en(row_repair_en)
      , .faulty_row_addr(faulty_row_addr)
    );
    repair_remap_row #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .NUM_SPARE_ROWS(2)
    ) u_repair_remap1 (
        .addr_in(sram_addr1)
      , .addr_out(sram_addr_phys1)
      , .row_repair_en(row_repair_en)
      , .faulty_row_addr(faulty_row_addr)
    );
    // Column redundancy: march-2rw's two ports are BOTH fully read/write and
    // can target DIFFERENT addresses the same cycle (E1/E4: port0 writes
    // addr_q, port1 writes partner_addr) -- no single reader/writer to
    // cross-wire one shared instance onto, unlike march-1r1w. Column
    // steering is address-independent (repair_remap_col has no address
    // input), so two fully independent instances compose safely: each
    // port's own instance only needs that port's own din_in/dout_in. Both
    // share col_repair_en/faulty_bit (one analyzer, one set of claims -- a
    // column claim is a property of a bit lane, not a port), but each has
    // ITS OWN spare_wen, since each port independently commits its own
    // writes (sram_model_2rw.sv).
    repair_remap_col #(
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_COLS(1)
    ) u_repair_remap_col0 (
        .din_in(sram_din0),
        .din_out(sram_din_phys0),
        .dout_in(sram_dout_phys0),
        .dout_out(sram_dout0),
        .spare_wen(sram_spare_wen0),
        .col_repair_en(col_repair_en),
        .faulty_bit(faulty_bit)
    );
    repair_remap_col #(
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_COLS(1)
    ) u_repair_remap_col1 (
        .din_in(sram_din1),
        .din_out(sram_din_phys1),
        .dout_in(sram_dout_phys1),
        .dout_out(sram_dout1),
        .spare_wen(sram_spare_wen1),
        .col_repair_en(col_repair_en),
        .faulty_bit(faulty_bit)
    );

    sram_s2rw #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
      , .NUM_SPARE_ROWS(2)
      , .NUM_SPARE_COLS(1)
    ) u_sram (
        .clk0(mbist_sram_clk0),
        .csb0(sram_csb0),
        .web0(sram_we0),
        .addr0(sram_addr_phys0),
        .din0(sram_din_phys0),
        .spare_wen0(sram_spare_wen0),
        .dout0(sram_dout_phys0),
        .clk1(mbist_sram_clk1),
        .csb1(sram_csb1),
        .web1(sram_we1),
        .addr1(sram_addr_phys1),
        .din1(sram_din_phys1),
        .spare_wen1(sram_spare_wen1),
        .dout1(sram_dout_phys1)
    );

endmodule
