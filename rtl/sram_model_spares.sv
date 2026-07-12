`timescale 1ns/1ps
// Spare-augmented single-port SRAM model (behavioral) for the redundancy-repair
// (BIRA/BISR) flow. It mirrors the STOCK OpenRAM single-port interface -- same
// clk0 / csb0 (active-low) / web0 (active-low, 0 = write) / addr0 / din0 / dout0
// convention as rtl/sram_model.sv -- with two deliberate differences:
//
//   * addr0 is widened to MEM_ADDR_WIDTH so the NUM_SPARE_ROWS spare rows are
//     addressable as the TOP addresses (2**ADDR_WIDTH .. 2**ADDR_WIDTH+spares-1),
//     exactly how OpenRAM exposes spare rows. There are NO repair pins on this
//     module: all repair/remap steering lives OUTSIDE, in rtl/repair_remap_row.sv.
//
//   * an OPTIONAL compile-time hard-defect knob (DEFECT_ADDR/BIT/SA1) forces a
//     genuine stuck-at into the stored array, so a repair loop has a real, fixed
//     defect to detect and steer around. DEFECT_ADDR < 0 disables it. A stock
//     OpenRAM macro cannot expose such a knob, which is exactly why it is a
//     parameter (baked into a test DUT copy), never a port.
module sram_model_spares #(
    parameter integer ADDR_WIDTH     = 10,   // LOGICAL address width (matches the wrapper)
    parameter integer DATA_WIDTH     = 32,
    parameter integer NUM_SPARE_ROWS = 1,
    // Physical address width incl. spare rows: ceil(log2(2**ADDR_WIDTH + spares)).
    // Derived default; the wrapper relies on it (equals SpareGeometry.mem_addr_width).
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = -1,   // -1 disables the hard-defect knob
    parameter integer DEFECT_BIT     = 0,
    parameter integer DEFECT_SA1     = 1     // 1 = stuck-at-1, 0 = stuck-at-0
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [DATA_WIDTH-1:0]       din0,
    output logic [DATA_WIDTH-1:0]       dout0
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK = (1 << DEFECT_BIT);

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
                // Genuine stuck-at at (DEFECT_ADDR, DEFECT_BIT): force the bit in
                // the STORED word, so every later read returns the stuck value.
                // SA1 -> OR the bit high; SA0 -> AND the bit low.
                mem[addr0] <= DEFECT_SA1 ? (din0 | DEFECT_MASK) : (din0 & ~DEFECT_MASK);
            end else begin
                mem[addr0] <= din0;
            end
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
