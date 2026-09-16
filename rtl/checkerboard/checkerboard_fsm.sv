`timescale 1ns/1ps

module checkerboard_fsm #(
    parameter integer ADDR_WIDTH   = 10,
    parameter integer DATA_WIDTH   = 32,
    parameter integer READ_LATENCY = 1
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  start,
    input  logic [DATA_WIDTH-1:0] mem_rdata,

    output logic                  mem_en,
    output logic                  mem_we,
    output logic [ADDR_WIDTH-1:0] mem_addr,
    output logic [DATA_WIDTH-1:0] mem_wdata,

    output logic                  busy,
    output logic                  done,
    output logic                  fail,

    // On-chip BIRA streaming interface (self-repair-ready from day one, mirroring
    // march_c_fsm.sv/march_raw_fsm.sv): fires the SAME cycle/condition that
    // already sets fail_q below, but exposes it as a live per-cell strobe
    // instead of only a sticky aggregate.
    output logic                  fail_valid,
    output logic [ADDR_WIDTH-1:0] fail_addr,

    // On-chip 2D (row+column) BIRA streaming interface: which specific bit(s)
    // mismatched, valid whenever fail_valid is. Per-bit `!==`, not a plain
    // XOR: bit-for-bit equivalent to fail_valid's own 4-state comparison
    // (XOR of an X bit is X, not 1, and would silently vanish from a "which
    // bits are set" scan) -- a latent-safety fix, not an active one, since
    // every current fault-injection model (saboteur_template.j2) only ever
    // produces known 0/1 on mem_rdata, never X.
    output logic [DATA_WIDTH-1:0] fail_bitmask
);

    // Checkerboard has 4 elements (phases 0-3), the same shape as march_x --
    // {either(wc); up(rc,wcb); down(rcb,wc); either(rc)} resolves to the
    // identical [up,up,down,down] direction sequence (resolve_directions()
    // depends only on each element's up/down/either marker, never op
    // content). LAST_PHASE and phase_is_up are sized for that sequence -- see
    // checkerboard_algo.sv's case table for the direction each phase runs.
    localparam logic [2:0] LAST_PHASE = 3'd3;
    localparam logic [ADDR_WIDTH-1:0] MAX_ADDR = {ADDR_WIDTH{1'b1}};

    typedef enum logic [2:0] {
        ST_IDLE  = 3'd0,
        ST_ISSUE = 3'd1,
        ST_WAIT  = 3'd2,
        ST_CHECK = 3'd3,
        ST_DONE  = 3'd4
    } checkerboard_state_t;

    checkerboard_state_t state_q;

    logic [2:0]            phase_q;
    logic                  substep_q;
    logic [ADDR_WIDTH-1:0] addr_q;
    logic [DATA_WIDTH-1:0] expected_q;
    logic                  fail_q;

    localparam integer WAIT_CNT_W = (READ_LATENCY < 2) ? 1 : $clog2(READ_LATENCY + 1);
    logic [WAIT_CNT_W-1:0] wait_cnt_q;

    logic                  phase_dir_up;
    logic                  do_read;
    logic                  do_write;
    logic [DATA_WIDTH-1:0] expected_data;
    logic [DATA_WIDTH-1:0] write_data;
    logic                  last_substep;

    checkerboard_algo #(
        .DATA_WIDTH(DATA_WIDTH)
    ) u_checkerboard_algo (
        .phase(phase_q),
        .op_step({1'b0, substep_q}),
        .addr_lsb(addr_q[0]),
        .phase_dir_up(phase_dir_up),
        .do_read(do_read),
        .do_write(do_write),
        .expected_data(expected_data),
        .write_data(write_data),
        .last_step(last_substep)
    );

    // Direction of a given phase: E0-E1 ascend, E2-E3 descend (matches
    // checkerboard_algo.sv's own phase_dir_up assignments -- this lookahead
    // must stay in lockstep with that table, see march_c_fsm.sv's identical
    // convention).
    function automatic logic phase_is_up(input logic [2:0] phase);
        phase_is_up = (phase <= 3'd1);
    endfunction

    assign mem_en    = (state_q == ST_ISSUE) && (do_read || do_write);
    assign mem_we    = (state_q == ST_ISSUE) && do_write;
    assign mem_addr  = addr_q;
    assign mem_wdata = write_data;

    assign busy = (state_q != ST_IDLE) && (state_q != ST_DONE);
    assign done = (state_q == ST_DONE);
    assign fail = fail_q;

    // Exactly the ST_CHECK compare condition below (fail_q's own trigger),
    // just also exposed live instead of only latched into a sticky bit.
    assign fail_valid = (state_q == ST_CHECK) && do_read && (mem_rdata !== expected_q);
    assign fail_addr  = addr_q;

    genvar gb;
    generate
        for (gb = 0; gb < DATA_WIDTH; gb++) begin : g_fail_bitmask
            assign fail_bitmask[gb] = (state_q == ST_CHECK) && do_read && (mem_rdata[gb] !== expected_q[gb]);
        end
    endgenerate

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q    <= ST_IDLE;
            phase_q    <= '0;
            substep_q  <= 1'b0;
            addr_q     <= '0;
            expected_q <= '0;
            fail_q     <= 1'b0;
            wait_cnt_q <= '0;
        end else begin
            case (state_q)
                ST_IDLE: begin
                    phase_q    <= '0;
                    substep_q  <= 1'b0;
                    addr_q     <= '0;
                    expected_q <= '0;
                    fail_q     <= 1'b0;
                    wait_cnt_q <= '0;
                    if (start) begin
                        state_q <= ST_ISSUE;
                    end
                end

                ST_ISSUE: begin
                    if (do_read) begin
                        expected_q <= expected_data;
                        if (READ_LATENCY == 0) begin
                            state_q <= ST_CHECK;
                        end else begin
                            wait_cnt_q <= READ_LATENCY - 1;
                            state_q    <= ST_WAIT;
                        end
                    end else begin
                        state_q <= ST_CHECK;
                    end
                end

                ST_WAIT: begin
                    if (wait_cnt_q == '0) begin
                        state_q <= ST_CHECK;
                    end else begin
                        wait_cnt_q <= wait_cnt_q - 1'b1;
                    end
                end

                ST_CHECK: begin
                    if (do_read && (mem_rdata !== expected_q)) begin
                        fail_q <= 1'b1;
                    end

                    if (last_substep) begin
                        if (phase_dir_up) begin
                            if (addr_q == MAX_ADDR) begin
                                if (phase_q == LAST_PHASE) begin
                                    state_q <= ST_DONE;
                                end else begin
                                    phase_q   <= phase_q + 1'b1;
                                    substep_q <= 1'b0;
                                    if (phase_is_up(phase_q + 1'b1)) begin
                                        addr_q <= '0;
                                    end else begin
                                        addr_q <= MAX_ADDR;
                                    end
                                    state_q <= ST_ISSUE;
                                end
                            end else begin
                                addr_q    <= addr_q + 1'b1;
                                substep_q <= 1'b0;
                                state_q   <= ST_ISSUE;
                            end
                        end else begin
                            if (addr_q == '0) begin
                                if (phase_q == LAST_PHASE) begin
                                    state_q <= ST_DONE;
                                end else begin
                                    phase_q   <= phase_q + 1'b1;
                                    substep_q <= 1'b0;
                                    if (phase_is_up(phase_q + 1'b1)) begin
                                        addr_q <= '0;
                                    end else begin
                                        addr_q <= MAX_ADDR;
                                    end
                                    state_q <= ST_ISSUE;
                                end
                            end else begin
                                addr_q    <= addr_q - 1'b1;
                                substep_q <= 1'b0;
                                state_q   <= ST_ISSUE;
                            end
                        end
                    end else begin
                        substep_q <= 1'b1;
                        state_q   <= ST_ISSUE;
                    end
                end

                ST_DONE: begin
                    if (!start) begin
                        state_q <= ST_IDLE;
                    end
                end

                default: begin
                    state_q <= ST_IDLE;
                end
            endcase
        end
    end

endmodule
