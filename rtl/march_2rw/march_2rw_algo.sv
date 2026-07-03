`timescale 1ns/1ps

// Combinational phase/op_step decoder for a 6-element 2RW concurrent march
// algorithm. Structured like march_1r1w_algo.sv, but generalized: every
// output is still a 2-element unpacked array indexed by physical port
// (index 0 / index 1), except NOW both do_read[k] and do_write[k] are
// legal for either k -- port 0 and port 1 are both fully read/write
// capable, so all four (do_read/do_write) x (port0/port1) combinations are
// physically meaningful, unlike march_1r1w_algo.sv where do_write[0] and
// do_read[1] were permanently tied off.
//
// Address scheme: the controller FSM (march_2rw_fsm.sv) sweeps a primary
// address addr_q across the full range every phase. Elements that need TWO
// distinct addresses on the same cycle (the write/write-different-address
// case) use addr_q for port 0 and a PARTNER address
// partner_addr = addr_q ^ (DEPTH/2) for port 1. XOR-ing in the top address
// bit always flips it, so partner_addr != addr_q for every possible addr_q
// across the whole sweep -- the two addresses are guaranteed different with
// no special-casing at the boundaries. Elements that need the SAME address
// on both ports (the read/read and read/write-forwarding cases) simply
// drive both ports from addr_q directly; the FSM applies partner_addr only
// where this decoder asks for it via use_partner_addr1.
//
// Timing note for the read(portA)/write(portB) same-address case (E3
// below): identical write-forwarding rule as march_1r1w_algo.sv's header
// comment, just generalized to whichever port is reading vs. writing this
// element -- in sram_model_2rw, a same-cycle write on one port commits (as
// a nonblocking assignment) on the SAME edge a concurrent read on the other
// port captures its addr_q into that port's addrN_q shadow register. The
// reading port's registered dout is driven from mem[addrN_q] on the
// FOLLOWING edge -- by which point the other port's same-cycle write has
// already landed in mem[]. So the read observes that write's own new data
// (write-forwarding), exactly like march_1r1w.
//
// Algorithm (march-C-sized, 6 elements):
//   E0 (up,   port0 write only):              write 0 (establish state)
//   E1 (up,   CONCURRENT WRITE/WRITE, diff addr):
//                                              port0 writes 1 @ addr_q,
//                                              port1 writes 1 @ partner_addr
//                                              (addr_q ^ DEPTH/2), same cycle
//   E2 (up,   CONCURRENT READ/READ, same addr):
//                                              port0 and port1 both read
//                                              addr_q, both expect 1
//   E3 (down, CONCURRENT READ(port0)/WRITE(port1), same addr, forwarding):
//                                              port1 writes 0 @ addr_q while
//                                              port0 reads addr_q same
//                                              cycle, expects 0 (that same
//                                              write's own new value)
//   E4 (down, CONCURRENT WRITE/WRITE, diff addr):
//                                              port0 writes 0 @ partner_addr,
//                                              port1 writes 0 @ addr_q,
//                                              same cycle (mirrors E1 with
//                                              the roles/values swapped)
//   E5 (down, port0 read only):               final read-only verification
//                                              pass: read addr_q, expect 0
//
// E1/E4 exercise genuine different-address, same-cycle write+write
// concurrency (both ports independently committing writes into the ONE
// shared array with zero interference) -- a case march_1r1w cannot express
// since it only has one write-capable port.
//
// E2 exercises genuine same-address, same-cycle read+read concurrency (two
// independent read paths against the one shared array observing the
// identical, correct value) -- a case march_1r1w cannot express since it
// only has one read-capable port.
//
// E3 exercises the 1R1W-style same-address read+write case, generalized so
// either port can play either role (here port0 reads, port1 writes).
module march_2rw_algo #(
    parameter integer DATA_WIDTH = 32
) (
    input  logic [2:0]            phase,
    output logic                  phase_dir_up,
    output logic                  do_read  [0:1],
    output logic                  do_write [0:1],
    output logic [DATA_WIDTH-1:0] expected_data [0:1],
    output logic [DATA_WIDTH-1:0] write_data    [0:1],
    // When asserted, the FSM drives port 1's address from partner_addr
    // (addr_q ^ DEPTH/2) instead of addr_q, so this element's port-1
    // access targets a DIFFERENT address than port 0's on the same cycle.
    output logic                  use_partner_addr1,
    output logic                  last_step
);

    localparam logic [DATA_WIDTH-1:0] ALL_ZERO = '0;
    localparam logic [DATA_WIDTH-1:0] ALL_ONE  = {DATA_WIDTH{1'b1}};

    always_comb begin
        phase_dir_up      = 1'b1;
        do_read[0]        = 1'b0;
        do_read[1]        = 1'b0;
        do_write[0]       = 1'b0;
        do_write[1]       = 1'b0;
        expected_data[0]  = ALL_ZERO;
        expected_data[1]  = ALL_ZERO;
        write_data[0]     = ALL_ZERO;
        write_data[1]     = ALL_ZERO;
        use_partner_addr1 = 1'b0;
        last_step         = 1'b1;

        case (phase)
            // E0: init -- write 0 via port 0 only, ascending.
            3'd0: begin
                phase_dir_up  = 1'b1;
                do_write[0]   = 1'b1;
                write_data[0] = ALL_ZERO;
                last_step     = 1'b1;
            end

            // E1: CONCURRENT WRITE/WRITE to DIFFERENT addresses, ascending.
            // port0 writes 1 @ addr_q; port1 writes 1 @ partner_addr.
            3'd1: begin
                phase_dir_up      = 1'b1;
                do_write[0]       = 1'b1;
                write_data[0]     = ALL_ONE;
                do_write[1]       = 1'b1;
                write_data[1]     = ALL_ONE;
                use_partner_addr1 = 1'b1;
                last_step         = 1'b1;
            end

            // E2: CONCURRENT READ/READ to the SAME address, ascending. Both
            // ports must observe the identical value written by E1.
            3'd2: begin
                phase_dir_up     = 1'b1;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ONE;
                do_read[1]       = 1'b1;
                expected_data[1] = ALL_ONE;
                last_step        = 1'b1;
            end

            // E3: CONCURRENT read(port0)/write(port1) to the SAME address,
            // descending -- write-forwarding case (1R1W-style, generalized).
            3'd3: begin
                phase_dir_up     = 1'b0;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ZERO;
                do_write[1]      = 1'b1;
                write_data[1]    = ALL_ZERO;
                last_step        = 1'b1;
            end

            // E4: CONCURRENT WRITE/WRITE to DIFFERENT addresses, descending
            // (same shape as E1: port0 writes @ addr_q, port1 writes @
            // partner_addr, both driving 0 so the final sweep in E5 reads
            // back 0 at every address, including the partner addresses
            // touched here).
            3'd4: begin
                phase_dir_up      = 1'b0;
                do_write[0]       = 1'b1;
                write_data[0]     = ALL_ZERO;
                do_write[1]       = 1'b1;
                write_data[1]     = ALL_ZERO;
                use_partner_addr1 = 1'b1;
                last_step         = 1'b1;
            end

            // E5: final read-only verification pass -- read 0 (port0 only),
            // descending.
            3'd5: begin
                phase_dir_up     = 1'b0;
                do_read[0]       = 1'b1;
                expected_data[0] = ALL_ZERO;
                last_step        = 1'b1;
            end

            default: begin
                phase_dir_up  = 1'b1;
                do_write[0]   = 1'b1;
                write_data[0] = ALL_ZERO;
                last_step     = 1'b1;
            end
        endcase
    end

endmodule
