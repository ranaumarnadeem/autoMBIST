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
//
//   * a SECOND, independent defect knob (DEFECT2_ADDR/BIT/SA1) exists purely to
//     let a test DUT force TWO simultaneous distinct-row defects -- e.g. to
//     prove BIRA correctly reports Unrepairable when the defect count exceeds
//     the spare budget. Disabled by default (DEFECT2_ADDR < 0), so every
//     existing single-defect instantiation is byte-for-byte unaffected.
//
//   * OPTIONAL spare COLUMNS (NUM_SPARE_COLS, default 0), mirroring OpenRAM's
//     spare-column interface: din0/dout0 widen to DATA_WIDTH+NUM_SPARE_COLS with
//     spare column k at bit DATA_WIDTH+k, and a spare_wen0 pin gates the spare
//     lanes' WRITES only (reads always return the full physical word, exactly as
//     on a real macro -- see flow/multimem/sky130_sram_32b512w.v:78-79). All
//     column steering lives OUTSIDE, in rtl/repair_remap_col.sv. At the default
//     NUM_SPARE_COLS=0 this module is bit-identical to before it existed:
//     MEM_DATA_WIDTH == DATA_WIDTH, [DATA_WIDTH-1:0] is the whole word, and the
//     spare-lane loop below never executes.
module sram_model_spares #(
    parameter integer ADDR_WIDTH     = 10,   // LOGICAL address width (matches the wrapper)
    parameter integer DATA_WIDTH     = 32,   // LOGICAL word width (no spare columns)
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 0,
    // Physical address width incl. spare rows: ceil(log2(2**ADDR_WIDTH + spares)).
    // Derived default; the wrapper relies on it (equals SpareGeometry.mem_addr_width).
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    // Physical data width incl. spare columns (equals SpareGeometry.mem_data_width).
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT_ADDR    = -1,   // -1 disables the hard-defect knob
    parameter integer DEFECT_BIT     = 0,
    parameter integer DEFECT_SA1     = 1,    // 1 = stuck-at-1, 0 = stuck-at-0
    parameter integer DEFECT2_ADDR   = -1,   // -1 disables the second defect knob
    parameter integer DEFECT2_BIT    = 0,
    parameter integer DEFECT2_SA1    = 1
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [MEM_DATA_WIDTH-1:0]   din0,
    output logic [MEM_DATA_WIDTH-1:0]   dout0,
    // Per-spare-column write enable, active high. Width is guarded so that
    // NUM_SPARE_COLS=0 gives a legal 1-bit port rather than [-1:0]; at that
    // setting nothing ever reads it, and existing instantiations simply leave
    // it unconnected.
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    // Defect masks stay at the LOGICAL width on purpose: the hard-defect knobs
    // model a defective real bitcell in the addressable word, and must never be
    // able to corrupt a spare lane (which would make a repair un-provable).
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    logic [MEM_DATA_WIDTH-1:0] mem [0:DEPTH-1];

    logic                      csb0_q;
    logic                      web0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;

        if (!csb0 && !web0) begin
            // LOGICAL half. Every store below is explicitly part-selected to
            // [DATA_WIDTH-1:0]: a whole-word store would write the spare lanes
            // straight from din0's top bits, BYPASSING spare_wen0 entirely and
            // breaking OpenRAM's contract -- the model would then pass
            // column-repair tests a real macro would fail. At NUM_SPARE_COLS=0
            // this part-select IS the whole word, so behaviour is unchanged.
            if (DEFECT_ADDR >= 0 && addr0 == DEFECT_ADDR) begin
                // Genuine stuck-at at (DEFECT_ADDR, DEFECT_BIT): force the bit in
                // the STORED word, so every later read returns the stuck value.
                // SA1 -> OR the bit high; SA0 -> AND the bit low.
                mem[addr0][DATA_WIDTH-1:0] <= DEFECT_SA1
                    ? (din0[DATA_WIDTH-1:0] |  DEFECT_MASK)
                    : (din0[DATA_WIDTH-1:0] & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr0 == DEFECT2_ADDR) begin
                // The second, independent defect (disabled by default -- see the
                // module header). Only reachable at all when DEFECT2_ADDR differs
                // from DEFECT_ADDR, since the first branch already claims a match
                // on DEFECT_ADDR.
                mem[addr0][DATA_WIDTH-1:0] <= DEFECT2_SA1
                    ? (din0[DATA_WIDTH-1:0] |  DEFECT2_MASK)
                    : (din0[DATA_WIDTH-1:0] & ~DEFECT2_MASK);
            end else begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0];
            end

            // SPARE lanes: written ONLY under their own spare_wen0 bit, never as
            // a side effect of the logical-word store above. Deliberately NOT
            // defect-aware -- a spare column is the good silicon a repair steers
            // onto. Never executes at NUM_SPARE_COLS=0.
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen0[k]) mem[addr0][DATA_WIDTH + k] <= din0[DATA_WIDTH + k];
            end
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
