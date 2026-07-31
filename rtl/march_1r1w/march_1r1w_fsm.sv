`timescale 1ns/1ps

// Controller FSM for the 6-element 1R1W concurrent march algorithm.
// Structured like march_c_fsm.sv (ST_IDLE/ST_ISSUE/ST_WAIT/ST_CHECK/
// ST_DONE), but drives BOTH ports out of the SAME ST_ISSUE cycle: the
// read-port (port 0) and write-port (port 1) control/address signals are
// combinationally derived from the identical state_q==ST_ISSUE condition
// and the identical address register addr_q, not sequenced across
// separate states. This is what makes the concurrency real rather than
// merely claimed -- there is exactly one address register and exactly one
// "issue" state driving both ports.
module march_1r1w_fsm #(
    parameter integer ADDR_WIDTH   = 10,
    parameter integer DATA_WIDTH   = 32,
    parameter integer READ_LATENCY = 1
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  start,
    input  logic [DATA_WIDTH-1:0] mem_rdata0,

    // Port 0: read-only.
    output logic                  mem_en0,
    output logic [ADDR_WIDTH-1:0] mem_addr0,

    // Port 1: write-only.
    output logic                  mem_en1,
    output logic [ADDR_WIDTH-1:0] mem_addr1,
    output logic [DATA_WIDTH-1:0] mem_wdata1,

    output logic                  busy,
    output logic                  done,
    output logic                  fail,

    // On-chip BIRA streaming interface (Step E): fires the SAME cycle/condition
    // that already sets fail_q below, but exposes it as a live per-cell strobe
    // instead of only a sticky aggregate -- an on-chip analyzer can register
    // every distinct failing row as the march passes over it, not just "some
    // row failed." Purely additive: no existing signal's timing changes. Only
    // port 0 (the read port) ever asserts a compare, so this stream is exactly
    // as single-fail-per-cycle as march_c_fsm's -- no arbitration needed.
    output logic                  fail_valid,
    output logic [ADDR_WIDTH-1:0] fail_addr
);

    localparam logic [2:0] LAST_PHASE = 3'd5;
    localparam logic [ADDR_WIDTH-1:0] MAX_ADDR = {ADDR_WIDTH{1'b1}};

    typedef enum logic [2:0] {
        ST_IDLE  = 3'd0,
        ST_ISSUE = 3'd1,
        ST_WAIT  = 3'd2,
        ST_CHECK = 3'd3,
        ST_DONE  = 3'd4
    } march_1r1w_state_t;

    march_1r1w_state_t state_q;

    logic [2:0]            phase_q;
    logic [ADDR_WIDTH-1:0] addr_q;
    logic [DATA_WIDTH-1:0] expected_q;
    logic                  fail_q;

    localparam integer WAIT_CNT_W = (READ_LATENCY < 2) ? 1 : $clog2(READ_LATENCY + 1);
    logic [WAIT_CNT_W-1:0] wait_cnt_q;

    logic                       phase_dir_up;
    logic [1:0]                 do_read;
    logic [1:0]                 do_write;
    logic [1:0][DATA_WIDTH-1:0] expected_data;
    logic [1:0][DATA_WIDTH-1:0] write_data;
    logic                       last_substep;

    march_1r1w_algo #(
        .DATA_WIDTH(DATA_WIDTH)
    ) u_march_1r1w_algo (
        .phase(phase_q),
        .phase_dir_up(phase_dir_up),
        .do_read(do_read),
        .do_write(do_write),
        .expected_data(expected_data),
        .write_data(write_data),
        .last_step(last_substep)
    );

    // Direction of a given phase: E0-E2 ascend, E3-E5 descend. Used to pick
    // the start address when transitioning INTO the next phase (mirrors
    // march_c_fsm's phase_is_up lookahead pattern).
    function automatic logic phase_is_up(input logic [2:0] phase);
        phase_is_up = (phase <= 3'd2);
    endfunction

    // Both ports are driven from the SAME state_q==ST_ISSUE condition and
    // the SAME addr_q register -- this is the concurrency guarantee.
    assign mem_en0    = (state_q == ST_ISSUE) && do_read[0];
    assign mem_addr0  = addr_q;

    assign mem_en1    = (state_q == ST_ISSUE) && do_write[1];
    assign mem_addr1  = addr_q;
    assign mem_wdata1 = write_data[1];

    assign busy = (state_q != ST_IDLE) && (state_q != ST_DONE);
    assign done = (state_q == ST_DONE);
    assign fail = fail_q;

    // Exactly the ST_CHECK compare condition below (fail_q's own trigger),
    // just also exposed live instead of only latched into a sticky bit.
    assign fail_valid = (state_q == ST_CHECK) && do_read[0] && (mem_rdata0 !== expected_q);
    assign fail_addr  = addr_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q    <= ST_IDLE;
            phase_q    <= '0;
            addr_q     <= '0;
            expected_q <= '0;
            fail_q     <= 1'b0;
            wait_cnt_q <= '0;
        end else begin
            case (state_q)
                ST_IDLE: begin
                    phase_q    <= '0;
                    addr_q     <= '0;
                    expected_q <= '0;
                    fail_q     <= 1'b0;
                    wait_cnt_q <= '0;
                    if (start) begin
                        state_q <= ST_ISSUE;
                    end
                end

                ST_ISSUE: begin
                    if (do_read[0]) begin
                        expected_q <= expected_data[0];
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
                    if (do_read[0] && (mem_rdata0 !== expected_q)) begin
                        fail_q <= 1'b1;
                    end

                    if (last_substep) begin
                        if (phase_dir_up) begin
                            if (addr_q == MAX_ADDR) begin
                                if (phase_q == LAST_PHASE) begin
                                    state_q <= ST_DONE;
                                end else begin
                                    phase_q <= phase_q + 1'b1;
                                    if (phase_is_up(phase_q + 1'b1)) begin
                                        addr_q <= '0;
                                    end else begin
                                        addr_q <= MAX_ADDR;
                                    end
                                    state_q <= ST_ISSUE;
                                end
                            end else begin
                                addr_q  <= addr_q + 1'b1;
                                state_q <= ST_ISSUE;
                            end
                        end else begin
                            if (addr_q == '0) begin
                                if (phase_q == LAST_PHASE) begin
                                    state_q <= ST_DONE;
                                end else begin
                                    phase_q <= phase_q + 1'b1;
                                    if (phase_is_up(phase_q + 1'b1)) begin
                                        addr_q <= '0;
                                    end else begin
                                        addr_q <= MAX_ADDR;
                                    end
                                    state_q <= ST_ISSUE;
                                end
                            end else begin
                                addr_q  <= addr_q - 1'b1;
                                state_q <= ST_ISSUE;
                            end
                        end
                    end else begin
                        state_q <= ST_ISSUE;
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
