`timescale 1ns/1ps
// A renamed copy of rtl/sram_model_spares.sv WITH SPARE COLUMNS ENABLED (module
// name changed to match the test config's memory_name; ports/logic otherwise
// identical to sram_spares_col_tiny.v's NUM_SPARE_COLS=1 shape). The DUT for
// the on-chip 2D analyzer's Finding-2 regression: a recurring bit that finds
// its ONE spare column already claimed by an EARLIER, unrelated recurrence
// must fall back to wanting a row claim instead of going straight to
// `unrepairable` -- see rtl/onchip_2d_repair_analyzer.sv's header, rule 3.
//
// FOUR defects, two "bit families" of two rows each:
//   * rows DEFECT1_ADDR/DEFECT2_ADDR both stuck-at on bit DEFECT1_BIT (0)
//   * rows DEFECT3_ADDR/DEFECT4_ADDR both stuck-at on bit DEFECT3_BIT (1)
// Whichever family's SECOND row is observed first wins the one spare column;
// the other family's second row must fall back to a row claim. Either way,
// this is exactly 2 "first sighting" row claims + 1 column claim + 1
// fallback row claim = 3 total row claims -- so with NUM_SPARE_ROWS=3 (driven
// by the wrapper instantiation, not baked in here) the whole chip is fully
// repairable regardless of march visitation order. A build that (incorrectly)
// sent the fallback straight to `unrepairable` instead of trying a row would
// leave this chip permanently flagged unrepairable even though 3 row spares
// were available the whole time -- see docs/redundancy-repair-plan.md.
//
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS ARE driven by the wrapper
// instantiation; the defaults here just document the intended shape.
module sram_spares_col_contention #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 3,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT1_ADDR   = 0,    // bit-0 family, first row
    parameter integer DEFECT1_BIT    = 0,
    parameter integer DEFECT2_ADDR   = 1,    // bit-0 family, second (recurring) row
    parameter integer DEFECT2_BIT    = 0,
    parameter integer DEFECT3_ADDR   = 2,    // bit-1 family, first row
    parameter integer DEFECT3_BIT    = 1,
    parameter integer DEFECT4_ADDR   = 3,    // bit-1 family, second (recurring) row
    parameter integer DEFECT4_BIT    = 1
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
    localparam logic [DATA_WIDTH-1:0] DEFECT1_MASK = (1 << DEFECT1_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT3_MASK = (1 << DEFECT3_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT4_MASK = (1 << DEFECT4_BIT);

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
            // as a side effect of the word store (see the reference model). All
            // four defects are stuck-at-1, so only the write-0 phases can ever
            // surface a mismatch -- matches every other fixture in this
            // directory's convention.
            if (DEFECT1_ADDR >= 0 && addr0 == DEFECT1_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT1_MASK;
            end else if (DEFECT2_ADDR >= 0 && addr0 == DEFECT2_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT2_MASK;
            end else if (DEFECT3_ADDR >= 0 && addr0 == DEFECT3_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT3_MASK;
            end else if (DEFECT4_ADDR >= 0 && addr0 == DEFECT4_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT4_MASK;
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
