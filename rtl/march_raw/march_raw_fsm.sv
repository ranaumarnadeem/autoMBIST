`timescale 1ns/1ps

module march_raw_fsm #(
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
    output logic                  fail
);

    localparam logic [2:0] LAST_PHASE = 3'd5;
    localparam logic [ADDR_WIDTH-1:0] MAX_ADDR = {ADDR_WIDTH{1'b1}};

    typedef enum logic [2:0] {
        ST_IDLE  = 3'd0,
        ST_ISSUE = 3'd1,
        ST_WAIT  = 3'd2,
        ST_CHECK = 3'd3,
        ST_DONE  = 3'd4
    } march_raw_state_t;

    march_raw_state_t state_q;

    logic [2:0]            phase_q;
    logic [1:0]            op_step_q;
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
    logic                  last_step;

    march_raw_algo #(
        .DATA_WIDTH(DATA_WIDTH)
    ) u_march_raw_algo (
        .phase(phase_q),
        .op_step(op_step_q),
        .phase_dir_up(phase_dir_up),
        .do_read(do_read),
        .do_write(do_write),
        .expected_data(expected_data),
        .write_data(write_data),
        .last_step(last_step)
    );

    function automatic logic phase_is_up(input logic [2:0] phase);
        phase_is_up = (phase <= 3'd2);
    endfunction

    assign mem_en    = (state_q == ST_ISSUE) && (do_read || do_write);
    assign mem_we    = (state_q == ST_ISSUE) && do_write;
    assign mem_addr  = addr_q;
    assign mem_wdata = write_data;

    assign busy = (state_q != ST_IDLE) && (state_q != ST_DONE);
    assign done = (state_q == ST_DONE);
    assign fail = fail_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q    <= ST_IDLE;
            phase_q    <= '0;
            op_step_q  <= '0;
            addr_q     <= '0;
            expected_q <= '0;
            fail_q     <= 1'b0;
            wait_cnt_q <= '0;
        end else begin
            case (state_q)
                ST_IDLE: begin
                    phase_q    <= '0;
                    op_step_q  <= '0;
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

                    if (last_step) begin
                        if (phase_dir_up) begin
                            if (addr_q == MAX_ADDR) begin
                                if (phase_q == LAST_PHASE) begin
                                    state_q <= ST_DONE;
                                end else begin
                                    phase_q   <= phase_q + 1'b1;
                                    op_step_q <= '0;
                                    if (phase_is_up(phase_q + 1'b1)) begin
                                        addr_q <= '0;
                                    end else begin
                                        addr_q <= MAX_ADDR;
                                    end
                                    state_q <= ST_ISSUE;
                                end
                            end else begin
                                addr_q    <= addr_q + 1'b1;
                                op_step_q <= '0;
                                state_q   <= ST_ISSUE;
                            end
                        end else begin
                            if (addr_q == '0) begin
                                if (phase_q == LAST_PHASE) begin
                                    state_q <= ST_DONE;
                                end else begin
                                    phase_q   <= phase_q + 1'b1;
                                    op_step_q <= '0;
                                    if (phase_is_up(phase_q + 1'b1)) begin
                                        addr_q <= '0;
                                    end else begin
                                        addr_q <= MAX_ADDR;
                                    end
                                    state_q <= ST_ISSUE;
                                end
                            end else begin
                                addr_q    <= addr_q - 1'b1;
                                op_step_q <= '0;
                                state_q   <= ST_ISSUE;
                            end
                        end
                    end else begin
                        op_step_q <= op_step_q + 1'b1;
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
