`timescale 1ns/1ps
// A renamed copy of rtl/sram_model_spares.sv WITH SPARE COLUMNS ENABLED (module
// name changed to match the test config's memory_name; ports/logic otherwise
// identical to the reference model's NUM_SPARE_COLS=1 shape). The DUT for the
// Workstream M.1 column-repair proof.
//
// The DEFECT_* parameter DEFAULTS are hardcoded here to the decisive
// column-repair scenario -- TWO stuck-at-1 defects on the SAME BIT (3) in
// DIFFERENT physical rows (1 and 2) -- because the wrapper generator never
// overrides them (it has no concept of a defect location), exactly as in
// sram_spares_tiny.v.
//
// Why that specific defect pair is the whole point: with num_spare_rows=1 and
// num_spare_cols=1, two faults in two distinct rows CANNOT be covered by the
// single spare ROW, so BIRA is forced to allocate the spare COLUMN instead
// (repair/bira.py's _forced_col finds bit 3 with row-degree 2 > the remaining
// row budget of 1). A mixed-bit defect pair would also pass with two spare
// rows and would therefore prove nothing about the column path.
//
// Bit 3 is the top logical bit, matching sram_spares_tiny.v's endianness-guard
// rationale -- and it sits directly below spare column 0 at bit DATA_WIDTH+0 = 4,
// so an off-by-one in the bit-steer mux shows up immediately rather than
// silently aliasing onto a neighbouring lane.
//
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS ARE driven by the wrapper
// instantiation; the defaults here just document the intended shape.
module sram_spares_col_tiny #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT_ADDR    = 1,    // physical row 1, ...
    parameter integer DEFECT_BIT     = 3,    // ... top logical bit
    parameter integer DEFECT_SA1     = 1,    // stuck-at-1
    parameter integer DEFECT2_ADDR   = 2,    // physical row 2, ...
    parameter integer DEFECT2_BIT    = 3,    // ... THE SAME BIT -- forces a column repair
    parameter integer DEFECT2_SA1    = 1
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [MEM_DATA_WIDTH-1:0]   din0,
    output logic [MEM_DATA_WIDTH-1:0]   dout0,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
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
            // LOGICAL half -- part-selected so a spare lane can NEVER be written
            // as a side effect of the word store (see the reference model).
            if (DEFECT_ADDR >= 0 && addr0 == DEFECT_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= DEFECT_SA1
                    ? (din0[DATA_WIDTH-1:0] |  DEFECT_MASK)
                    : (din0[DATA_WIDTH-1:0] & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr0 == DEFECT2_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= DEFECT2_SA1
                    ? (din0[DATA_WIDTH-1:0] |  DEFECT2_MASK)
                    : (din0[DATA_WIDTH-1:0] & ~DEFECT2_MASK);
            end else begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0];
            end

            // SPARE lanes: written ONLY under spare_wen0, and never defect-aware.
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen0[k]) mem[addr0][DATA_WIDTH + k] <= din0[DATA_WIDTH + k];
            end
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
