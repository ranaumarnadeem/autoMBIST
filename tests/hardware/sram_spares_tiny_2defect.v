`timescale 1ns/1ps
// A renamed copy of rtl/sram_model_spares.sv (module name changed to match the
// test config's memory_name; ports/logic otherwise identical) with BOTH defect
// knobs baked in, at two DISTINCT logical rows -- unlike sram_spares_tiny.v
// (one defect), this DUT exists specifically to give the Step-D end-to-end
// unrepairable-case test a genuine "more distinct faulty rows than spares"
// scenario: row 3 (stuck-at-1, bit 3 -- the same known-good defect
// sram_spares_tiny.v uses) AND row 1 (stuck-at-0, bit 1 -- a different row,
// bit, and stuck-at polarity, so this isn't just a copy of the first defect).
// With NUM_SPARE_ROWS=1, BIRA's row-only allocation cannot cover 2 distinct
// faulty rows -- exactly the case the roadmap's DVCon-style recipe calls for
// proving is correctly FLAGGED, not silently passed.
module sram_spares_tiny_2defect #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = 3,    // physical row 3 is defective
    parameter integer DEFECT_BIT     = 3,    // top bit (endianness guard)
    parameter integer DEFECT_SA1     = 1,    // stuck-at-1
    parameter integer DEFECT2_ADDR   = 1,    // physical row 1 is ALSO defective
    parameter integer DEFECT2_BIT    = 1,
    parameter integer DEFECT2_SA1    = 0     // stuck-at-0 (different polarity)
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [DATA_WIDTH-1:0]       din0,
    output logic [DATA_WIDTH-1:0]       dout0
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    logic [DATA_WIDTH-1:0]     mem [0:DEPTH-1];

    logic                      csb0_q;
    logic                      web0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;

        if (!csb0 && !web0) begin
            if (DEFECT_ADDR >= 0 && addr0 == DEFECT_ADDR) begin
                mem[addr0] <= DEFECT_SA1 ? (din0 | DEFECT_MASK) : (din0 & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr0 == DEFECT2_ADDR) begin
                mem[addr0] <= DEFECT2_SA1 ? (din0 | DEFECT2_MASK) : (din0 & ~DEFECT2_MASK);
            end else begin
                mem[addr0] <= din0;
            end
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
