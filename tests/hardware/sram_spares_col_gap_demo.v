`timescale 1ns/1ps
// A renamed copy of rtl/sram_model_spares.sv WITH SPARE COLUMNS ENABLED (module
// name changed to match the test config's memory_name; ports/logic otherwise
// identical to sram_spares_col_tiny.v's shape). The DUT for the on-chip 2D
// analyzer's DISCLOSED gap: a 5-fault set that repair.bira.analyze() proves
// has a complete repair (verified directly against this repo's own bira.py:
// `RepairSolution(row_map={5: 0, 30: 1}, col_map={2: 0})`, 2 spare rows + 1
// spare col), but which the single-pass, no-lookahead on-chip heuristic
// cannot fully resolve -- see rtl/onchip_2d_repair_analyzer.sv's header.
//
// Five defects, all stuck-at-1:
//   * row DEFECT_A_ADDR (5): BOTH bits DEFECT_A_BIT0 (0) and DEFECT_A_BIT1 (1)
//   * row DEFECT_B_ADDR (9): bit DEFECT_BC_BIT (2)
//   * row DEFECT_C_ADDR (20): bit DEFECT_BC_BIT (2) -- SAME bit as row 9
//   * row DEFECT_D_ADDR (30): bit DEFECT_D_BIT (5)
//
// Why this is unrepairable on-chip regardless of march visitation order: the
// fault set contains exactly THREE distinct "first ever sighting" events that
// each default to wanting a row claim (rule 4, no column fallback on a first
// sighting) -- row 5's word, whichever of rows 9/20 is visited first (bit 2's
// first sighting), and row 30 -- competing for only NUM_SPARE_ROWS=2 (driven
// by the wrapper instantiation). Whichever of the three is visited LAST finds
// no row spare free and is never retried; row 2's recurrence (whichever of
// 9/20 is visited SECOND) always succeeds via the one spare column, since
// nothing else in this fault set ever contests it. BIRA's real backtracking
// search sees the WHOLE fault set at once and correctly allocates rows 5 and
// 30 to spares, then bit 2 to the column -- a solution this one-pass streaming
// analyzer structurally cannot discover, since row 30 hasn't been observed yet
// at the moment row 9 (or 20)'s row-claim decision is made. This is the real,
// disclosed severity of the on-chip heuristic's gap: it can report a
// repairable chip unrepairable -- never a false pass (verify-by-re-execution
// is independent of the analyzer's own bookkeeping) -- see
// docs/redundancy-repair-plan.md.
//
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS ARE driven by the wrapper
// instantiation; the defaults here just document the intended shape.
module sram_spares_col_gap_demo #(
    parameter integer ADDR_WIDTH     = 5,
    parameter integer DATA_WIDTH     = 8,
    parameter integer NUM_SPARE_ROWS = 2,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT_A_ADDR  = 5,
    parameter integer DEFECT_A_BIT0  = 0,
    parameter integer DEFECT_A_BIT1  = 1,
    parameter integer DEFECT_B_ADDR  = 9,
    parameter integer DEFECT_C_ADDR  = 20,
    parameter integer DEFECT_BC_BIT  = 2,
    parameter integer DEFECT_D_ADDR  = 30,
    parameter integer DEFECT_D_BIT   = 5
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
    localparam logic [DATA_WIDTH-1:0] DEFECT_A_MASK = (1 << DEFECT_A_BIT0) | (1 << DEFECT_A_BIT1);
    localparam logic [DATA_WIDTH-1:0] DEFECT_BC_MASK = (1 << DEFECT_BC_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT_D_MASK = (1 << DEFECT_D_BIT);

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
            // defects are stuck-at-1, so only the write-0 phases can ever
            // surface a mismatch.
            if (DEFECT_A_ADDR >= 0 && addr0 == DEFECT_A_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT_A_MASK;
            end else if (DEFECT_B_ADDR >= 0 && addr0 == DEFECT_B_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT_BC_MASK;
            end else if (DEFECT_C_ADDR >= 0 && addr0 == DEFECT_C_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT_BC_MASK;
            end else if (DEFECT_D_ADDR >= 0 && addr0 == DEFECT_D_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT_D_MASK;
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
