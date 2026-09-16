`timescale 1ns/1ps

// Checkerboard (6n): { either(wc); up(rc,wcb); down(rcb,wc); either(rc) } --
// src/autombist/algos/checkerboard.alg. Logical/address-LSB-parity
// checkerboard (NOT physical row/column adjacency -- no consumer in this
// toolkit has physical geometry): every cell holds the opposite value of its
// address-LSB neighbor. The first classic-path algo module whose value
// depends on address, not just (phase, op_step) -- write_data/expected_data
// are {DATA_WIDTH{addr_lsb}} (or its complement), mirroring the research-shell
// engine's own wc/wcb/rc/rcb semantics (addr & 1, an LSB not a full XOR
// parity -- see alg_spec.py's AccessStep.write_value / march_engine.sv).
module checkerboard_algo #(
    parameter integer DATA_WIDTH = 32
) (
    input  logic [2:0]            phase,
    input  logic [1:0]            op_step,
    input  logic                  addr_lsb,
    output logic                  phase_dir_up,
    output logic                  do_read,
    output logic                  do_write,
    output logic [DATA_WIDTH-1:0] expected_data,
    output logic [DATA_WIDTH-1:0] write_data,
    output logic                  last_step
);

    always_comb begin
        phase_dir_up  = 1'b1;
        do_read       = 1'b0;
        do_write      = 1'b0;
        expected_data = '0;
        write_data    = '0;
        last_step     = 1'b1;

        case (phase)
            3'd0: begin  // either wc
                phase_dir_up = 1'b1;
                do_write     = 1'b1;
                write_data   = {DATA_WIDTH{addr_lsb}};
                last_step    = 1'b1;
            end

            3'd1: begin  // up rc wcb
                phase_dir_up = 1'b1;
                if (op_step == 2'd0) begin
                    do_read       = 1'b1;
                    expected_data = {DATA_WIDTH{addr_lsb}};
                    last_step     = 1'b0;
                end else begin
                    do_write     = 1'b1;
                    write_data   = {DATA_WIDTH{~addr_lsb}};
                    last_step    = 1'b1;
                end
            end

            3'd2: begin  // down rcb wc
                phase_dir_up = 1'b0;
                if (op_step == 2'd0) begin
                    do_read       = 1'b1;
                    expected_data = {DATA_WIDTH{~addr_lsb}};
                    last_step     = 1'b0;
                end else begin
                    do_write     = 1'b1;
                    write_data   = {DATA_WIDTH{addr_lsb}};
                    last_step    = 1'b1;
                end
            end

            3'd3: begin  // either rc
                phase_dir_up  = 1'b0;
                do_read       = 1'b1;
                expected_data = {DATA_WIDTH{addr_lsb}};
                last_step     = 1'b1;
            end

            default: begin
                phase_dir_up = 1'b1;
                do_write     = 1'b1;
                write_data   = '0;
                last_step    = 1'b1;
            end
        endcase
    end

endmodule
