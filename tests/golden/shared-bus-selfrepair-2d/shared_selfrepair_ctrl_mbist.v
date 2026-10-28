`timescale 1ns/1ps

module shared_selfrepair_ctrl #(
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

    // Shared-controller self-repair (docs/shared-hierarchical-mbist-plan.md
    // §9b): one independent onchip_selfrepair_ctrl + onchip_row_repair_analyzer
    // + repair_remap_row per memory (generate loop, below, inside the
    // is_shared_bus block where the outer sequencer lives) -- each memory's
    // own defects are independent, so its own repair state must be too. Only
    // algo_fail_valid/algo_fail_addr stay singular/broadcast: u_algo_top
    // itself is singular (unaware of which memory it's driving, per step 1),
    // so its own per-pass fail report is read by whichever memory's analyzer
    // currently has registrar_enable_arr[mem_sel_q] asserted -- broadcasting
    // is safe here because every OTHER instance's own registrar_enable stays
    // deasserted unless it's genuinely that memory's turn (see §4c's own
    // "broadcast is safe when downstream semantics are gated per-instance"
    // principle). effective_test_mode is declared here too (still singular --
    // one wrapper-level mux signal) but its own assign lives inside the
    // is_shared_bus block below, where shb_selfrepair_mode_q/
    // ctrl_test_mode_override_arr exist -- NOT the assign above, which would
    // be a second, conflicting driver of the same net.
    logic                  algo_fail_valid;
    logic [ADDR_WIDTH-1:0] algo_fail_addr;
    logic                  effective_test_mode;
    // Packed per-memory arrays -- see the note at sram_csb_arr below.
    logic [2-1:0] self_repair_start_arr;
    logic [2-1:0] self_repair_done_arr;
    logic [2-1:0] self_repair_fail_arr;
    logic [2-1:0] self_repair_busy_arr;
    logic [2-1:0] ctrl_test_mode_override_arr;
    logic [2-1:0] ctrl_bist_start_arr;
    logic [2-1:0] registrar_enable_arr;
    logic [2-1:0] latch_result_arr;
    logic [2-1:0] unrepairable_arr;
    logic [2-1:0][2-1:0]            row_repair_en_arr;
    logic [2-1:0][2*ADDR_WIDTH-1:0] faulty_row_addr_arr;
    logic [2-1:0][5-1:0]            sram_addr_phys_arr;
    // On-chip 2D (row+column) self-repair, shared-bus: same per-memory
    // partitioning as the row-only arrays above, extended for the column
    // side. algo_fail_bitmask is singular/broadcast for the same reason
    // algo_fail_valid/algo_fail_addr are (see comment above) -- u_algo_top
    // itself has no per-memory concept.
    logic [DATA_WIDTH-1:0]                                  algo_fail_bitmask;
    logic [2-1:0][1-1:0]                            col_repair_en_arr;
    logic [2-1:0][1*3-1:0] faulty_bit_arr;
    logic [2-1:0][9-1:0]                            sram_din_phys_arr;
    logic [2-1:0][9-1:0]                            sram_dout_phys_arr;
    logic [2-1:0][1-1:0]                            spare_wen_arr;
    // Sequences u_algo_top across mem_sel_q = 0..NUM_MEMORIES-1: SHB_RUN holds
    // algo_bist_start high while one memory's pass runs; on algo_bist_done,
    // SHB_ADVANCE drops algo_bist_start for exactly one cycle (forcing
    // u_algo_top's own FSM back to its idle state via its existing
    // bist_start-drop restart path -- no new reset signal needed, see
    // docs/shared-hierarchical-mbist-plan.md §4a) before SHB_RUN re-raises it
    // for the next memory. u_algo_top itself needs zero awareness of which
    // memory it's driving -- this sequencer and the csb/dout mux below sit
    // strictly between it and the memory instantiation(s), and its own
    // address/data/write-enable path (sram_addr/sram_din/sram_we above) is
    // completely unchanged.
    typedef enum logic [1:0] {
        SHB_IDLE,
        SHB_RUN,
        SHB_ADVANCE,
        SHB_DONE
    } shb_state_t;

    shb_state_t             shb_state_q;
    logic [1-1:0] mem_sel_q;
    logic                    algo_bist_start;
    logic                    algo_bist_done;
    logic                    algo_bist_fail;
    logic                    shb_fail_q;
    // Every per-memory *_arr in this wrapper is a PACKED array: Yosys lowers
    // an unpacked array read with a variable index (sram_dout_arr[mem_sel_q])
    // to a $mem whose write side never sees the instance-port connections, so
    // the memories' outputs drive nothing and opt_clean deletes the memories.
    // Indexing semantics are identical either way.
    logic [2-1:0]                 sram_csb_arr;
    logic [2-1:0][DATA_WIDTH-1:0] sram_dout_arr;
    // Second orchestration mode, docs/shared-hierarchical-mbist-plan.md §9b:
    // shb_selfrepair_mode_q, latched only at the SHB_IDLE exit that fires,
    // selects whether SHB_RUN/SHB_DONE are running one plain algo pass per
    // memory (mode_q=0, byte-identical to the has_onchip_selfrepair=false
    // case below) or one full two-pass (analyze+verify) repair sequence per
    // memory (mode_q=1) -- reusing the same four states, since both shapes
    // are "wait for a trigger / run one pass and wait for its own done /
    // advance to the next memory / hold the aggregate result"; only what
    // counts as "this pass is done" and what drives u_algo_top differ.
    logic                    shb_selfrepair_mode_q;
    logic                    shb_selfrepair_done_q;
    logic                    shb_selfrepair_fail_q;

    // One independent onchip_row_repair_analyzer + onchip_selfrepair_ctrl +
    // repair_remap_row per memory -- each reads u_algo_top's own PER-PASS
    // algo_bist_done/algo_bist_fail directly (not the wrapper's aggregate
    // bist_done/bist_fail, which only fires after ALL memories finish).
    // mbist_busy/algo_fail_valid/algo_fail_addr stay singular (u_algo_top's
    // own outputs, broadcast) -- see the declaration block above for why
    // broadcasting fail_valid/fail_addr is safe here.
    genvar gsr;
    generate
      for (gsr = 0; gsr < 2; gsr++) begin : selfrepair_inst
        onchip_2d_repair_analyzer #(
            .ADDR_WIDTH(ADDR_WIDTH),
            .DATA_WIDTH(DATA_WIDTH),
            .NUM_SPARE_ROWS(2),
            .NUM_SPARE_COLS(1)
        ) u_onchip_analyzer (
            .clk(clk),
            .rst_n(rst_n),
            .enable(registrar_enable_arr[gsr]),
            .fail_valid(algo_fail_valid),
            .fail_addr(algo_fail_addr),
            .fail_bitmask(algo_fail_bitmask),
            .latch_result(latch_result_arr[gsr]),
            .repair_load(1'b0),
            .fuse_row_repair_en('0),
            .fuse_faulty_row_addr('0),
            .repair_load_done(),
            .row_repair_en(row_repair_en_arr[gsr]),
            .faulty_row_addr(faulty_row_addr_arr[gsr]),
            .col_repair_en(col_repair_en_arr[gsr]),
            .faulty_bit(faulty_bit_arr[gsr]),
            .unrepairable(unrepairable_arr[gsr]),
            .verify_enable(1'b0),
            .retire(1'b0),
            .has_suspect(),
            .defer(1'b0)
        );

        onchip_selfrepair_ctrl u_onchip_selfrepair_ctrl (
            .clk(clk),
            .rst_n(rst_n),
            .self_repair_start(self_repair_start_arr[gsr]),
            .self_repair_done(self_repair_done_arr[gsr]),
            .self_repair_fail(self_repair_fail_arr[gsr]),
            .self_repair_busy(self_repair_busy_arr[gsr]),
            .ctrl_test_mode_override(ctrl_test_mode_override_arr[gsr]),
            .ctrl_bist_start(ctrl_bist_start_arr[gsr]),
            .mbist_busy(mbist_busy),
            .bist_done(algo_bist_done),
            .bist_fail(algo_bist_fail),
            .registrar_enable(registrar_enable_arr[gsr]),
            .latch_result(latch_result_arr[gsr]),
            .unrepairable(unrepairable_arr[gsr]),
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
            .addr_out(sram_addr_phys_arr[gsr])
          , .row_repair_en(row_repair_en_arr[gsr])
          , .faulty_row_addr(faulty_row_addr_arr[gsr])
        );

        repair_remap_col #(
            .DATA_WIDTH(DATA_WIDTH),
            .NUM_SPARE_COLS(1)
        ) u_repair_remap_col (
            .din_in(sram_din),
            .din_out(sram_din_phys_arr[gsr]),
            .dout_in(sram_dout_phys_arr[gsr]),
            .dout_out(sram_dout_arr[gsr]),
            .spare_wen(spare_wen_arr[gsr]),
            .col_repair_en(col_repair_en_arr[gsr]),
            .faulty_bit(faulty_bit_arr[gsr])
        );
      end
    endgenerate

    assign algo_bist_start = shb_selfrepair_mode_q
        ? ctrl_bist_start_arr[mem_sel_q]
        : (shb_state_q == SHB_RUN);
    assign effective_test_mode = test_mode
        || (shb_selfrepair_mode_q && ctrl_test_mode_override_arr[mem_sel_q]);
    assign bist_done       = (shb_state_q == SHB_DONE) && !shb_selfrepair_mode_q;
    assign self_repair_busy = shb_selfrepair_mode_q && (shb_state_q != SHB_IDLE);
    assign self_repair_done = shb_selfrepair_done_q;
    assign self_repair_fail = shb_selfrepair_fail_q;
    assign self_repair_start_arr[0] =
        shb_selfrepair_mode_q && (shb_state_q != SHB_IDLE) && (mem_sel_q == 0);
    assign self_repair_start_arr[1] =
        shb_selfrepair_mode_q && (shb_state_q != SHB_IDLE) && (mem_sel_q == 1);
    assign bist_fail       = shb_fail_q;
    assign sram_dout       = sram_dout_arr[mem_sel_q];
    assign sram_csb_arr[0] = (mem_sel_q == 0) ? sram_csb : 1'b1;
    assign sram_csb_arr[1] = (mem_sel_q == 1) ? sram_csb : 1'b1;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shb_state_q <= SHB_IDLE;
            mem_sel_q   <= '0;
            shb_fail_q  <= 1'b0;
            shb_selfrepair_mode_q <= 1'b0;
            shb_selfrepair_done_q <= 1'b0;
            shb_selfrepair_fail_q <= 1'b0;
        end else begin
            case (shb_state_q)
                SHB_IDLE: begin
                    if (self_repair_start && !mbist_busy) begin
                        mem_sel_q              <= '0;
                        shb_selfrepair_done_q  <= 1'b0;
                        shb_selfrepair_fail_q  <= 1'b0;
                        shb_selfrepair_mode_q  <= 1'b1;
                        shb_state_q            <= SHB_RUN;
                    end else if (bist_start && test_mode && !self_repair_start) begin
                        mem_sel_q              <= '0;
                        shb_fail_q             <= 1'b0;
                        shb_selfrepair_mode_q  <= 1'b0;
                        shb_state_q            <= SHB_RUN;
                    end
                end
                SHB_RUN: begin
                    if (shb_selfrepair_mode_q) begin
                        if (self_repair_done_arr[mem_sel_q]) begin
                            if (self_repair_fail_arr[mem_sel_q])
                                shb_selfrepair_fail_q <= 1'b1;
                            if (mem_sel_q == 1) begin
                                shb_selfrepair_done_q <= 1'b1;
                                shb_state_q            <= SHB_DONE;
                            end else begin
                                shb_state_q <= SHB_ADVANCE;
                            end
                        end
                    end else begin
                        if (algo_bist_done) begin
                            if (algo_bist_fail) shb_fail_q <= 1'b1;
                            if (mem_sel_q == 1)
                                shb_state_q <= SHB_DONE;
                            else
                                shb_state_q <= SHB_ADVANCE;
                        end
                    end
                end
                SHB_ADVANCE: begin
                    mem_sel_q   <= mem_sel_q + 1'b1;
                    shb_state_q <= SHB_RUN;
                end
                SHB_DONE: begin
                    if (shb_selfrepair_mode_q) begin
                        if (!self_repair_start) shb_state_q <= SHB_IDLE;
                    end else begin
                        if (!(bist_start && test_mode)) shb_state_q <= SHB_IDLE;
                    end
                end
                default: shb_state_q <= SHB_IDLE;
            endcase
        end
    end

    march_c_top #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(1)
    ) u_algo_top (
        .clk(clk),
        .rst_n(rst_n),
        .bist_start(algo_bist_start),
        .bist_busy(mbist_busy),
        .bist_done(algo_bist_done),
        .bist_fail(algo_bist_fail),
        .bist_fail_valid(algo_fail_valid),
        .bist_fail_addr(algo_fail_addr),
        .bist_fail_bitmask(algo_fail_bitmask),
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

    // Shared-controller self-repair (docs/shared-hierarchical-mbist-plan.md
    // §9b): the per-memory analyzer/ctrl/remap instances already exist above,
    // inside the is_shared_bus generate loop (selfrepair_inst) -- nothing to
    // instantiate here. sram_addr_phys_arr (declared alongside that loop) is
    // what the memory instantiation below reads.

    sram_bank_spares #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_ROWS(2),
        .NUM_SPARE_COLS(1)
    ) u_mem_mem_bank0 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[0]),
        .addr0(sram_addr_phys_arr[0]),
        .din0(sram_din_phys_arr[0]),
        .web0(sram_we),
        .dout0(sram_dout_phys_arr[0])
      , .spare_wen0(spare_wen_arr[0])
    );
    sram_bank_spares #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_ROWS(2),
        .NUM_SPARE_COLS(1)
    ) u_mem_mem_bank1 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[1]),
        .addr0(sram_addr_phys_arr[1]),
        .din0(sram_din_phys_arr[1]),
        .web0(sram_we),
        .dout0(sram_dout_phys_arr[1])
      , .spare_wen0(spare_wen_arr[1])
    );

endmodule
