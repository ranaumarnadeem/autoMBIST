`timescale 1ns/1ps

// Behavioral 2-read-write-port SRAM model.
//
// This is a genuinely dual-ported model over a SINGLE shared mem[] array --
// NOT two independent sram_model.sv instances, and NOT a reuse/wrapper of
// sram_model_1r1w.sv. Unlike sram_model_1r1w (port 0 structurally read-only,
// port 1 structurally write-only), BOTH ports here are fully read/write
// capable: port 0 (clk0/csb0/web0/addr0/din0/dout0) and port 1
// (clk1/csb1/web1/addr1/din1/dout1) each independently support a read or a
// write on any given cycle, so a concurrent march element can have either
// port do either operation -- including both ports writing different
// addresses the same cycle, or both ports reading the same address the same
// cycle, neither of which sram_model_1r1w can express.
//
// Per-port timing mirrors sram_model_1r1w.sv/sram_model.sv exactly:
//   - Read: registered 1-cycle. csbN/webN/addrN are sampled into _q shadow
//     registers at posedge clkN; when the sampled csbN_q/webN_q indicate a
//     read (csb low, web high), doutN is driven from mem[addrN_q] on the
//     FOLLOWING edge.
//   - Write: same-cycle, non-blocking. When !csbN && !webN on a given edge,
//     mem[addrN] <= dinN commits on that same edge.
//
// clk0 and clk1 are expected to be tied to the same clock by the
// instantiating testbench/wrapper (this module makes no such assumption
// itself).
//
// Write-forwarding timing note (mirrors march_1r1w_algo.sv's header
// comment, generalized to either port): because a read is REGISTERED
// (addrN is captured into addrN_q on the issuing edge; doutN <= mem[addrN_q]
// is driven on the FOLLOWING edge) while a write on the OTHER port commits
// immediately (as a non-blocking assignment on the SAME edge), a same-
// address, same-cycle read-on-one-port + write-on-the-other-port access
// observes write-forwarding: by the time the reading port's doutN is
// actually driven (one edge later), the other port's same-cycle write has
// already landed in mem[]. So such an access reads that write's own new
// value, not whatever preceded it.
//
// Same-address, same-cycle WRITE + WRITE on both ports is intentionally
// left unspecified here (real dual-port SRAM compilers do not define this
// either) -- no march_2rw algorithm element issues that combination, so this
// model does not attempt to arbitrate it.
module sram_model_2rw #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer DATA_WIDTH = 32
) (
    // Port 0: full read/write.
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic                  web0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    input  logic [DATA_WIDTH-1:0] din0,
    output logic [DATA_WIDTH-1:0] dout0,

    // Port 1: full read/write.
    input  logic                  clk1,
    input  logic                  csb1,
    input  logic                  web1,
    input  logic [ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0] din1,
    output logic [DATA_WIDTH-1:0] dout1
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);

    // Exactly ONE shared storage array indexed by both ports.
    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    logic                  csb0_q;
    logic                  web0_q;
    logic [ADDR_WIDTH-1:0] addr0_q;

    logic                  csb1_q;
    logic                  web1_q;
    logic [ADDR_WIDTH-1:0] addr1_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;

        if (!csb0 && !web0) begin
            mem[addr0] <= din0;
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

    always_ff @(posedge clk1) begin
        csb1_q  <= csb1;
        web1_q  <= web1;
        addr1_q <= addr1;

        if (!csb1 && !web1) begin
            mem[addr1] <= din1;
        end

        if (!csb1_q && web1_q) begin
            dout1 <= mem[addr1_q];
        end
    end

endmodule
