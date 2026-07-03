`timescale 1ns/1ps

// Test-only DUT macro for the 1R1W integration e2e path: a renamed copy of
// rtl/sram_model_1r1w.sv (module name changed to match the test config's
// memory_name; ports otherwise identical), so the generated wrapper's
// per-port pin names line up exactly with this module's ports without any
// extra translation layer. See tests/integration/test_1r1w_e2e.py.
//
// Genuinely dual-ported over a SINGLE shared mem[] array -- NOT two
// independent single-port instances. Port 0 is read-only (registered
// 1-cycle read); port 1 is write-only (same-cycle write). Both index into
// the same underlying storage, so a write on port 1 can be observed by a
// read on port 0, which is the whole point of a concurrent 1R1W model.
module sram_1r1w_dut #(
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
