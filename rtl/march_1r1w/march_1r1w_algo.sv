`timescale 1ns/1ps

// Combinational phase/op_step decoder for a 6-element 1R1W concurrent march
// algorithm. Structured like march_c_algo.sv, but every output is a
// 2-element unpacked array indexed by physical port: index 0 is the
// read-only port, index 1 is the write-only port. do_write[0] and
// do_read[1] are never asserted -- port 0 physically cannot write and
// port 1 physically cannot read -- the array shape exists purely for
// uniformity with the later march_2rw generalization, where both ports
// can do either.
//
// The two ports never access one address in the same cycle: a compiled
// two-port macro (OpenRAM's among them) leaves a read of the address the
// other port is writing undefined. In an element with both a read and a
// write, march_1r1w_fsm.sv has port 0 read the address the sweep is at
// while port 1 writes the address read on the previous step, so a read
// sees what the PREVIOUS element left in that cell. The reads go through
// port 0 and the writes through port 1, so the read path and the write
// path must both reach the same storage cells for the checks to pass.
//
// Algorithm (March C-, its reads on port 0 and writes on port 1):
//   E0 (up,   port1 only):            w0
//   E1 (up,   CONCURRENT r0+w1):      r0, then w1 one step later
//   E2 (up,   CONCURRENT r0+w1):      r1, then w0 one step later
//   E3 (down, CONCURRENT r0+w1):      r0, then w1 one step later
//   E4 (down, CONCURRENT r0+w1):      r1, then w0 one step later
//   E5 (down, port0 only):            r0
//
// In elements 1-4 every step reads one address on port 0 while port 1
// writes the neighbouring one in the same cycle.
// Ports below use packed (not unpacked) 2-element vectors -- Yosys's
// read_verilog frontend rejects unpacked-array port declarations
// (`do_read [0:1]`) with a syntax error; packed `[1:0]` indexes identically
// (do_read[0], do_read[1]) at every call site, so this is a synthesis-target
// fix with no semantic change.
module march_1r1w_algo #(
    parameter integer DATA_WIDTH = 32
) (
    input  logic [2:0]                 phase,
    output logic                       phase_dir_up,
    output logic [1:0]                 do_read,
    output logic [1:0]                 do_write,
    output logic [1:0][DATA_WIDTH-1:0] expected_data,
    output logic [1:0][DATA_WIDTH-1:0] write_data,
    output logic                       last_step
);

    localparam logic [DATA_WIDTH-1:0] ALL_ZERO = '0;
    localparam logic [DATA_WIDTH-1:0] ALL_ONE  = {DATA_WIDTH{1'b1}};

    always_comb begin
        phase_dir_up     = 1'b1;
        do_read[0]       = 1'b0;
        do_read[1]       = 1'b0;
        do_write[0]      = 1'b0;
        do_write[1]      = 1'b0;
        expected_data[0] = ALL_ZERO;
        expected_data[1] = ALL_ZERO;
        write_data[0]    = ALL_ZERO;
        write_data[1]    = ALL_ZERO;
        last_step        = 1'b1;

        case (phase)
            // E0: init -- write 0 via port 1, ascending. Port 0 idle.
            3'd0: begin
                phase_dir_up = 1'b1;
                do_write[1]  = 1'b1;
                write_data[1] = ALL_ZERO;
                last_step    = 1'b1;
            end

            // E1: read 0 (port0) while port1 writes 1 one address behind,
            // ascending, CONCURRENT.
            3'd1: begin
                phase_dir_up     = 1'b1;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ZERO;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ONE;
                last_step        = 1'b1;
            end

            // E2: read 1 (port0) while port1 writes 0 one address behind,
            // ascending, CONCURRENT.
            3'd2: begin
                phase_dir_up     = 1'b1;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ONE;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ZERO;
                last_step        = 1'b1;
            end

            // E3: read 0 (port0) while port1 writes 1 one address behind,
            // descending, CONCURRENT.
            3'd3: begin
                phase_dir_up     = 1'b0;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ZERO;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ONE;
                last_step        = 1'b1;
            end

            // E4: read 1 (port0) while port1 writes 0 one address behind,
            // descending, CONCURRENT.
            3'd4: begin
                phase_dir_up     = 1'b0;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ONE;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ZERO;
                last_step        = 1'b1;
            end

            // E5: final check -- read 0 (port0 only), descending.
            3'd5: begin
                phase_dir_up     = 1'b0;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ZERO;
                last_step        = 1'b1;
            end

            default: begin
                phase_dir_up = 1'b1;
                do_write[1]  = 1'b1;
                write_data[1] = ALL_ZERO;
                last_step    = 1'b1;
            end
        endcase
    end

endmodule
