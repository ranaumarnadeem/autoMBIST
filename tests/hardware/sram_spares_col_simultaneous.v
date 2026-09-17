`timescale 1ns/1ps
// A renamed copy of rtl/sram_model_spares.sv WITH SPARE COLUMNS ENABLED (module
// name changed to match the test config's memory_name; ports/logic otherwise
// identical to sram_spares_col_tiny.v's shape, generalized to a per-defect
// BITMASK rather than a single bit). The DUT for the on-chip 2D analyzer's
// Finding-1 regression: two bits failing in the SAME cycle, both already
// `seen_once`, must not race for the same column slot from a stale snapshot.
//
// Two rows, ROW_A and ROW_B, each with BOTH bits DEFECT_BIT0 and DEFECT_BIT1
// stuck-at-1 SIMULTANEOUSLY. Whichever row is observed first is a genuine
// first-ever sighting of both bits -- a single word-level row claim covers
// it (rule 4, "row wins ties"), marking both bits seen_once. Whichever row is
// observed SECOND then has BOTH of its bits already seen_once, and BOTH
// attempt a column claim in the identical cycle (rule 3) -- exactly the race
// a naive independent-per-bit "find the first free slot" implementation would
// lose (both bits computing the same target slot from the same pre-cycle
// snapshot, one silently clobbering the other). With NUM_SPARE_COLS=2 (driven
// by the wrapper instantiation) both bits have a slot available IF claimed
// correctly via a sequential priority chain; a buggy racing implementation
// would leave one of the two bits permanently unrepaired even though a slot
// existed for it. Symmetric in ROW_A/ROW_B, so the result does not depend on
// which physical row a given march algorithm happens to visit first -- see
// docs/redundancy-repair-plan.md.
//
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS ARE driven by the wrapper
// instantiation; the defaults here just document the intended shape.
module sram_spares_col_simultaneous #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 2,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer ROW_A_ADDR     = 0,
    parameter integer ROW_B_ADDR     = 1,
    parameter integer DEFECT_BIT0    = 0,
    parameter integer DEFECT_BIT1    = 1
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
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK = (1 << DEFECT_BIT0) | (1 << DEFECT_BIT1);

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
            // as a side effect of the word store (see the reference model). Both
            // defect bits are stuck-at-1, so only the write-0 phases can ever
            // surface a mismatch, and both bits mismatch on the SAME cycle --
            // the simultaneity this fixture exists to exercise.
            if (ROW_A_ADDR >= 0 && addr0 == ROW_A_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT_MASK;
            end else if (ROW_B_ADDR >= 0 && addr0 == ROW_B_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0] | DEFECT_MASK;
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
