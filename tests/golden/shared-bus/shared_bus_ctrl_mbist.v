`timescale 1ns/1ps

module shared_bus_ctrl #(
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
    logic [2-1:0] mem_sel_q;
    logic                    algo_bist_start;
    logic                    algo_bist_done;
    logic                    algo_bist_fail;
    logic                    shb_fail_q;
    // Every per-memory *_arr in this wrapper is a PACKED array: Yosys lowers
    // an unpacked array read with a variable index (sram_dout_arr[mem_sel_q])
    // to a $mem whose write side never sees the instance-port connections, so
    // the memories' outputs drive nothing and opt_clean deletes the memories.
    // Indexing semantics are identical either way.
    logic [3-1:0]                 sram_csb_arr;
    logic [3-1:0][DATA_WIDTH-1:0] sram_dout_arr;

    assign algo_bist_start = (shb_state_q == SHB_RUN);
    assign bist_done       = (shb_state_q == SHB_DONE);
    assign bist_fail       = shb_fail_q;
    assign sram_dout       = sram_dout_arr[mem_sel_q];
    assign sram_csb_arr[0] = (mem_sel_q == 0) ? sram_csb : 1'b1;
    assign sram_csb_arr[1] = (mem_sel_q == 1) ? sram_csb : 1'b1;
    assign sram_csb_arr[2] = (mem_sel_q == 2) ? sram_csb : 1'b1;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shb_state_q <= SHB_IDLE;
            mem_sel_q   <= '0;
            shb_fail_q  <= 1'b0;
        end else begin
            case (shb_state_q)
                SHB_IDLE: begin
                    if (bist_start && test_mode) begin
                        mem_sel_q   <= '0;
                        shb_fail_q  <= 1'b0;
                        shb_state_q <= SHB_RUN;
                    end
                end
                SHB_RUN: begin
                    if (algo_bist_done) begin
                        if (algo_bist_fail) shb_fail_q <= 1'b1;
                        if (mem_sel_q == 2)
                            shb_state_q <= SHB_DONE;
                        else
                            shb_state_q <= SHB_ADVANCE;
                    end
                end
                SHB_ADVANCE: begin
                    mem_sel_q   <= mem_sel_q + 1'b1;
                    shb_state_q <= SHB_RUN;
                end
                SHB_DONE: begin
                    // Parenthesized: start_signal (bist_start && test_mode in
                    // the non-self-repair case) must be negated as a whole --
                    // !bist_start && test_mode is NOT !(bist_start && test_mode).
                    if (!(bist_start && test_mode)) shb_state_q <= SHB_IDLE;
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

    sram_bank #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_mem_mem_bank0 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[0]),
        .addr0(sram_addr),
        .din0(sram_din),
        .web0(sram_we),
        .dout0(sram_dout_arr[0])
    );
    sram_bank #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_mem_mem_bank1 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[1]),
        .addr0(sram_addr),
        .din0(sram_din),
        .web0(sram_we),
        .dout0(sram_dout_arr[1])
    );
    sram_bank #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_mem_mem_bank2 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[2]),
        .addr0(sram_addr),
        .din0(sram_din),
        .web0(sram_we),
        .dout0(sram_dout_arr[2])
    );

endmodule
