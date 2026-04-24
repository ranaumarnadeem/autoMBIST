`timescale 1ns/1ps

module mbist_algo #(
    parameter integer DATA_WIDTH = 32
) (
    input  logic [2:0]            phase,
    input  logic                  substep,
    output logic                  phase_dir_up,
    output logic                  do_read,
    output logic                  do_write,
    output logic [DATA_WIDTH-1:0] expected_data,
    output logic [DATA_WIDTH-1:0] write_data,
    output logic                  last_substep
);

    localparam logic [DATA_WIDTH-1:0] ALL_ZERO = '0;
    localparam logic [DATA_WIDTH-1:0] ALL_ONE  = {DATA_WIDTH{1'b1}};

    always_comb begin
        phase_dir_up  = 1'b1;
        do_read       = 1'b0;
        do_write      = 1'b0;
        expected_data = ALL_ZERO;
        write_data    = ALL_ZERO;
        last_substep  = 1'b1;

        case (phase)
            3'd0: begin
                // up(w0)
                phase_dir_up = 1'b1;
                do_write     = 1'b1;
                write_data   = ALL_ZERO;
                last_substep = 1'b1;
            end

            3'd1: begin
                // up(r0, w1)
                phase_dir_up = 1'b1;
                if (!substep) begin
                    do_read       = 1'b1;
                    expected_data = ALL_ZERO;
                    last_substep  = 1'b0;
                end else begin
                    do_write     = 1'b1;
                    write_data   = ALL_ONE;
                    last_substep = 1'b1;
                end
            end

            3'd2: begin
                // up(r1, w0)
                phase_dir_up = 1'b1;
                if (!substep) begin
                    do_read       = 1'b1;
                    expected_data = ALL_ONE;
                    last_substep  = 1'b0;
                end else begin
                    do_write     = 1'b1;
                    write_data   = ALL_ZERO;
                    last_substep = 1'b1;
                end
            end

            3'd3: begin
                // down(r0, w1)
                phase_dir_up = 1'b0;
                if (!substep) begin
                    do_read       = 1'b1;
                    expected_data = ALL_ZERO;
                    last_substep  = 1'b0;
                end else begin
                    do_write     = 1'b1;
                    write_data   = ALL_ONE;
                    last_substep = 1'b1;
                end
            end

            3'd4: begin
                // down(r1, w0)
                phase_dir_up = 1'b0;
                if (!substep) begin
                    do_read       = 1'b1;
                    expected_data = ALL_ONE;
                    last_substep  = 1'b0;
                end else begin
                    do_write     = 1'b1;
                    write_data   = ALL_ZERO;
                    last_substep = 1'b1;
                end
            end

            3'd5: begin
                // down(r0)
                phase_dir_up  = 1'b0;
                do_read       = 1'b1;
                expected_data = ALL_ZERO;
                last_substep  = 1'b1;
            end

            default: begin
                phase_dir_up = 1'b1;
                do_write     = 1'b1;
                write_data   = ALL_ZERO;
                last_substep = 1'b1;
            end
        endcase
    end

endmodule
