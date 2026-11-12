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

    // One functional port per memory: outside test mode every memory is
    // reached through its own port. Only BIST time-multiplexes them.
    input  logic                  func_csb_mem_bank0,
    input  logic [ADDR_WIDTH-1:0] func_addr_mem_bank0,
    input  logic [DATA_WIDTH-1:0] func_din_mem_bank0,
    input  logic                  func_we_mem_bank0,
    output logic [DATA_WIDTH-1:0] func_dout_mem_bank0,
    input  logic                  func_csb_mem_bank1,
    input  logic [ADDR_WIDTH-1:0] func_addr_mem_bank1,
    input  logic [DATA_WIDTH-1:0] func_din_mem_bank1,
    input  logic                  func_we_mem_bank1,
    output logic [DATA_WIDTH-1:0] func_dout_mem_bank1,
    input  logic                  func_csb_mem_bank2,
    input  logic [ADDR_WIDTH-1:0] func_addr_mem_bank2,
    input  logic [DATA_WIDTH-1:0] func_din_mem_bank2,
    input  logic                  func_we_mem_bank2,
    output logic [DATA_WIDTH-1:0] func_dout_mem_bank2
);

    logic                  mbist_busy;
    logic                  mbist_sram_clk;
    logic                  mbist_sram_csb;
    logic                  mbist_sram_web;
    logic [ADDR_WIDTH-1:0] mbist_sram_addr;
    logic [DATA_WIDTH-1:0] mbist_sram_din;

    logic [DATA_WIDTH-1:0] sram_dout;
    logic [DATA_WIDTH-1:0] sram_dbg_actual_word;
    logic [DATA_WIDTH-1:0] sram_dbg_fault_word;
    logic [DATA_WIDTH-1:0] sram_dbg_intended_word;
    logic [DATA_WIDTH-1:0] sram_dbg_prior_value;
    logic [DATA_WIDTH-1:0] sram_dbg_attempted_write;
    logic [DATA_WIDTH-1:0] sram_dbg_actual_read;
    logic [DATA_WIDTH-1:0] sram_dbg_blocked_bits;
    logic mbist_write_req;

    // Sequences u_algo_top across mem_sel_q = 0..NUM_MEMORIES-1: SHB_RUN holds
    // algo_bist_start high while one memory's pass runs; on algo_bist_done,
    // SHB_ADVANCE drops algo_bist_start for exactly one cycle (forcing
    // u_algo_top's own FSM back to its idle state via its existing
    // bist_start-drop restart path -- no new reset signal needed, see
    // docs/shared-hierarchical-mbist-plan.md §4a) before SHB_RUN re-raises it
    // for the next memory. u_algo_top itself needs zero awareness of which
    // memory it's driving -- this sequencer and the per-memory muxes below sit
    // strictly between it and the memory instantiation(s). In test mode every
    // memory sees the controller's address/data/write enable and only the
    // selected one is enabled; outside test mode each memory is driven by its
    // own func_*_<name> port.
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
    logic [3-1:0]                 sram_we_arr;
    logic [3-1:0][ADDR_WIDTH-1:0] sram_addr_arr;
    logic [3-1:0][DATA_WIDTH-1:0] sram_din_arr;
    logic [3-1:0][DATA_WIDTH-1:0] sram_dout_arr;

    assign algo_bist_start = (shb_state_q == SHB_RUN);
    assign bist_done       = (shb_state_q == SHB_DONE);
    assign bist_fail       = shb_fail_q;
    assign sram_dout       = sram_dout_arr[mem_sel_q];

    // mem_bank0: the controller's selected memory in test mode, its own
    // functional port otherwise.
    assign sram_csb_arr[0]  = test_mode ? ((mem_sel_q == 0) ? mbist_sram_csb : 1'b1)
                                    : func_csb_mem_bank0;
    assign sram_addr_arr[0] = test_mode ? mbist_sram_addr : func_addr_mem_bank0;
    assign sram_din_arr[0]  = test_mode ? mbist_sram_din : func_din_mem_bank0;
    assign sram_we_arr[0]   = ~(test_mode ? mbist_write_req : func_we_mem_bank0);
    assign func_dout_mem_bank0 = sram_dout_arr[0];

    // mem_bank1: the controller's selected memory in test mode, its own
    // functional port otherwise.
    assign sram_csb_arr[1]  = test_mode ? ((mem_sel_q == 1) ? mbist_sram_csb : 1'b1)
                                    : func_csb_mem_bank1;
    assign sram_addr_arr[1] = test_mode ? mbist_sram_addr : func_addr_mem_bank1;
    assign sram_din_arr[1]  = test_mode ? mbist_sram_din : func_din_mem_bank1;
    assign sram_we_arr[1]   = ~(test_mode ? mbist_write_req : func_we_mem_bank1);
    assign func_dout_mem_bank1 = sram_dout_arr[1];

    // mem_bank2: the controller's selected memory in test mode, its own
    // functional port otherwise.
    assign sram_csb_arr[2]  = test_mode ? ((mem_sel_q == 2) ? mbist_sram_csb : 1'b1)
                                    : func_csb_mem_bank2;
    assign sram_addr_arr[2] = test_mode ? mbist_sram_addr : func_addr_mem_bank2;
    assign sram_din_arr[2]  = test_mode ? mbist_sram_din : func_din_mem_bank2;
    assign sram_we_arr[2]   = ~(test_mode ? mbist_write_req : func_we_mem_bank2);
    assign func_dout_mem_bank2 = sram_dout_arr[2];

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

    sram_bank #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_mem_mem_bank0 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[0]),
        .addr0(sram_addr_arr[0]),
        .din0(sram_din_arr[0]),
        .web0(sram_we_arr[0]),
        .dout0(sram_dout_arr[0])
    );
    sram_bank #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_mem_mem_bank1 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[1]),
        .addr0(sram_addr_arr[1]),
        .din0(sram_din_arr[1]),
        .web0(sram_we_arr[1]),
        .dout0(sram_dout_arr[1])
    );
    sram_bank #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_mem_mem_bank2 (
        .clk0(mbist_sram_clk),
        .csb0(sram_csb_arr[2]),
        .addr0(sram_addr_arr[2]),
        .din0(sram_din_arr[2]),
        .web0(sram_we_arr[2]),
        .dout0(sram_dout_arr[2])
    );

endmodule
