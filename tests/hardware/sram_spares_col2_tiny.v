`timescale 1ns/1ps
// A renamed copy of rtl/sram_model_spares.sv WITH SPARE COLUMNS ENABLED, DUT B
// for the Workstream M.1 column-repair proof (sibling of sram_spares_col_tiny.v).
//
// Where DUT A (sram_spares_col_tiny.v) puts both defects on the SAME bit to
// FORCE a column repair, this one puts them on DIFFERENT rows AND DIFFERENT
// bits -- (1, bit 0) and (2, bit 3) -- so that with num_spare_rows=1 and
// num_spare_cols=1 BIRA allocates ONE OF EACH: row 1 to the spare row, bit 3 to
// the spare column. That is the row+column COEXISTENCE proof.
//
// Traced against repair/bira.py, not assumed: neither dimension is forced in
// the must-repair phase (every row has column-degree 1, every column has
// row-degree 1, and both budgets are 1), so _solve_residual branches on the
// canonical min fault (1,0) -> repairs row 1 -> the residual {(2,3)} has no row
// budget left -> repairs column 3. Deterministic: row_map={1:0}, col_map={3:0}.
//
// What that arrangement additionally proves, which DUT A cannot:
//   * the defect at (2, bit 3) sits on a row the row-remap does NOT steer, so
//     its repair genuinely comes from the column path, not incidentally from
//     the spare row;
//   * reading logical row 1 (steered to the spare row) still gets its bit 3
//     from the spare COLUMN -- i.e. the spare row's own spare column works,
//     which is the composition property the two remaps must have.
//
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS ARE driven by the wrapper
// instantiation; the defaults here just document the intended shape.
module sram_spares_col2_tiny #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT_ADDR    = 1,    // physical row 1, ...
    parameter integer DEFECT_BIT     = 0,    // ... BOTTOM logical bit
    parameter integer DEFECT_SA1     = 1,    // stuck-at-1
    parameter integer DEFECT2_ADDR   = 2,    // physical row 2, ...
    parameter integer DEFECT2_BIT    = 3,    // ... TOP logical bit (different row AND bit)
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
