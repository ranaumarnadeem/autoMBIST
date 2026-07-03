`timescale 1ns/1ps

// Test-only DUT macro for the 2RW integration e2e path: a renamed copy of
// rtl/sram_model_2rw.sv (module name changed to match the test config's
// memory_name; ports otherwise identical), so the generated wrapper's
// per-port pin names line up exactly with this module's ports without any
// extra translation layer. See tests/integration/test_2rw_e2e.py.
//
// Genuinely dual-ported over a SINGLE shared mem[] array -- NOT two
// independent single-port instances. BOTH ports are fully read/write
// capable (unlike sram_1r1w_dut.v, whose port 0 is read-only and port 1 is
// write-only): port 0 (clk0/csb0/web0/addr0/din0/dout0) and port 1
// (clk1/csb1/web1/addr1/din1/dout1) each independently support a read or a
// write on any given cycle, so a concurrent march element can have either
// port do either operation.
module sram_2rw_dut #(
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
