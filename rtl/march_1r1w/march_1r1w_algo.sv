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
// Timing note (why expected_data looks the way it does below): in
// sram_model_1r1w, a port-1 write to address A commits (as a nonblocking
// assignment) on the SAME edge that a concurrent port-0 read captures
// addr0_q = A. Port 0's registered read then drives dout0 <= mem[addr0_q]
// on the FOLLOWING edge -- by which point that write has already landed in
// mem[]. The net effect: a read issued on the same cycle as a same-address
// write observes that write's OWN new data (write-forwarding), not
// whatever was there before. This is a legitimate, and in fact the more
// interesting, definition of same-address concurrent 1R1W access -- it
// means the read path and the write path must be correctly bridged through
// the SAME shared storage cell on the SAME cycle for the check to pass,
// which is exactly the port-coupling property this phase exists to prove.
// A stuck-at bit on the shared cell, or a broken address/data route
// between port 1's write and port 0's read, both show up immediately as a
// same-cycle mismatch.
//
// Algorithm (march-C-sized, 6 elements):
//   E0 (up,   port1 only):            write 0
//   E1 (up,   CONCURRENT r0+w1):      write 1 while reading that same
//                                      same-cycle write back (expect 1)
//   E2 (up,   CONCURRENT r0+w1):      write 0 while reading that same
//                                      same-cycle write back (expect 0)
//   E3 (down, CONCURRENT r0+w1):      write 1 while reading that same
//                                      same-cycle write back (expect 1)
//   E4 (down, CONCURRENT r0+w1):      write 0 while reading that same
//                                      same-cycle write back (expect 0)
//   E5 (down, port0 only):            read back E4's final value (expect 0)
//
// Elements 1-4 exercise genuine same-address, same-cycle read(port0) +
// write(port1) concurrency -- exactly the condition an inter-port coupling
// fault needs to be sensitized.
module march_1r1w_algo #(
    parameter integer DATA_WIDTH = 32
) (
    input  logic [2:0]            phase,
    output logic                  phase_dir_up,
    output logic                  do_read  [0:1],
    output logic                  do_write [0:1],
    output logic [DATA_WIDTH-1:0] expected_data [0:1],
    output logic [DATA_WIDTH-1:0] write_data    [0:1],
    output logic                  last_step
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

            // E1: write 1 (port1) while reading that same write back
            // (port0), ascending, CONCURRENT.
            3'd1: begin
                phase_dir_up     = 1'b1;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ONE;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ONE;
                last_step        = 1'b1;
            end

            // E2: write 0 (port1) while reading that same write back
            // (port0), ascending, CONCURRENT.
            3'd2: begin
                phase_dir_up     = 1'b1;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ZERO;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ZERO;
                last_step        = 1'b1;
            end

            // E3: write 1 (port1) while reading that same write back
            // (port0), descending, CONCURRENT.
            3'd3: begin
                phase_dir_up     = 1'b0;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ONE;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ONE;
                last_step        = 1'b1;
            end

            // E4: write 0 (port1) while reading that same write back
            // (port0), descending, CONCURRENT.
            3'd4: begin
                phase_dir_up     = 1'b0;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ZERO;
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
