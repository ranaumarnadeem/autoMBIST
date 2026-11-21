`timescale 1ns/1ps
// The 1-read + 1-write port version of sram_spares_muxed_col.v: a COLUMN-MUXED
// memory with spare rows and spare columns, WORDS_PER_ROW = 2 (the value
// OpenRAM picks for sky130_sram_1r1w_32b256w). Port 0 is read-only and
// registered, port 1 is write-only and carries spare_wen1; both share one
// array.
//
// The spare columns bypass the column mux, so each has ONE cell per physical
// row (spare rows included), SHARED by the row's words: a write with
// spare_wen1[k] stores din1[DATA_WIDTH + k] in it whichever word it addresses,
// and a read of either word of the row returns it on dout0[DATA_WIDTH + k].
//
// The planted defect is a BITLINE defect: bit DEFECT_BIT of word column
// DEFECT_WCOL is stuck-at-1 in every physical row, spare rows included --
// physical column DEFECT_BIT * 2 + DEFECT_WCOL (5 by default: addresses 1, 3,
// 5, 7 and the spare row's 9, bit 2). Addresses past the last spare row do not
// exist: writes are dropped and reads return X.
//
// WORDS_PER_ROW and the defect are baked in; the wrapper passes only
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS. Used by
// tests/integration/test_repair_muxed_col_multiport_e2e.py.
module sram_spares_muxed_col_1r1w #(
    parameter integer ADDR_WIDTH     = 3,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS * 2),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT_WCOL    = 1,
    parameter integer DEFECT_BIT     = 2
) (
    // Port 0: read-only.
    input  logic                      clk0,
    input  logic                      csb0,
    input  logic [MEM_ADDR_WIDTH-1:0] addr0,
    output logic [MEM_DATA_WIDTH-1:0] dout0,

    // Port 1: write-only.
    input  logic                      clk1,
    input  logic                      csb1,
    input  logic                      web1,
    input  logic [MEM_ADDR_WIDTH-1:0] addr1,
    input  logic [MEM_DATA_WIDTH-1:0] din1,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen1
);

    localparam integer WORDS_PER_ROW = 2;
    localparam integer COL_BITS      = 1;
    localparam integer PHYS_WORDS    = (1 << ADDR_WIDTH) + NUM_SPARE_ROWS * WORDS_PER_ROW;
    localparam integer PHYS_ROWS     = PHYS_WORDS / WORDS_PER_ROW;

    logic [DATA_WIDTH-1:0]     mem   [0:PHYS_WORDS-1];
    logic [NUM_SPARE_COLS-1:0] spare [0:PHYS_ROWS-1];  // one cell per physical row per spare

    logic                      csb0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        addr0_q <= addr0;
        if (!csb0_q) begin
            dout0 <= (addr0_q < PHYS_WORDS) ? {spare[addr0_q >> COL_BITS], mem[addr0_q]} : 'x;
        end
    end

    always_ff @(posedge clk1) begin
        if (!csb1 && !web1 && addr1 < PHYS_WORDS) begin
            mem[addr1] <= din1[DATA_WIDTH-1:0]
                | ((addr1 % WORDS_PER_ROW == DEFECT_WCOL) ? (1 << DEFECT_BIT) : 0);
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen1[k]) spare[addr1 >> COL_BITS][k] <= din1[DATA_WIDTH + k];
            end
        end
    end

endmodule
