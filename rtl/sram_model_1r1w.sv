`timescale 1ns/1ps

// Behavioral 1-read-port + 1-write-port SRAM model.
//
// This is a genuinely dual-ported model over a SINGLE shared mem[] array --
// NOT two independent sram_model.sv instances. Port 0 is read-only and
// port 1 is write-only, but both index into the same underlying storage, so
// a write issued on port 1 at address A can be observed by a read issued on
// port 0 at address A on a later (or, per the timing below, the very same)
// clock edge. That inter-port coupling is the whole point of this model:
// without it, no concurrent 1R1W MBIST algorithm could ever prove that its
// same-cycle read+write actually touches shared state.
//
// Port 0 (read-only): registered 1-cycle read, same timing convention as
// sram_model.sv -- csb0/addr0 are sampled into _q shadow registers at
// posedge clk0, and dout0 is driven from mem[addr0_q] on the following
// edge.
//
// Port 1 (write-only): same-cycle (non-blocking, same posedge) write into
// mem[addr1] when !csb1 && !web1, matching sram_model.sv write timing.
//
// clk0 and clk1 are expected to be tied to the same clock by the
// instantiating testbench/wrapper (this module makes no such assumption
// itself). Because port 0's read is REGISTERED (addr0 is captured into
// addr0_q on this edge; dout0 <= mem[addr0_q] is driven on the FOLLOWING
// edge), a port-1 write to address A and a port-0 read of address A issued
// on the very same edge observe write-forwarding: by the time port 0's
// dout0 is actually driven (one edge later), port 1's same-cycle write has
// already landed in mem[]. So a same-address, same-cycle concurrent access
// reads that write's own new value, not whatever preceded it. See
// march_1r1w_algo.sv's header comment for why this is the correct (and
// more interesting) definition of same-address concurrent 1R1W access to
// verify against.
module sram_model_1r1w #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer DATA_WIDTH = 32
) (
    // Port 0: read-only.
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    output logic [DATA_WIDTH-1:0] dout0,

    // Port 1: write-only.
    input  logic                  clk1,
    input  logic                  csb1,
    input  logic                  web1,
    input  logic [ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0] din1
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);

    // Exactly ONE shared storage array indexed by both ports.
    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    logic                  csb0_q;
    logic [ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        addr0_q <= addr0;

        if (!csb0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

    always_ff @(posedge clk1) begin
        if (!csb1 && !web1) begin
            mem[addr1] <= din1;
        end
    end

endmodule
