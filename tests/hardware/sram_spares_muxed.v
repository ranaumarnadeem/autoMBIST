`timescale 1ns/1ps
// A COLUMN-MUXED spare-augmented memory, laid out the way OpenRAM lays one out:
// WORDS_PER_ROW consecutive addresses share a physical row (the low address
// bits pick the column), and the NUM_SPARE_ROWS spare rows follow the last
// regular row, so spare row i holds addresses 2**ADDR_WIDTH + i*WORDS_PER_ROW
// onwards. Addresses past the last spare row do not exist: writes to them are
// dropped and reads return X, so a remap that steers off the real array fails.
//
// The planted defect is a WORDLINE defect: bit DEFECT_BIT is stuck-at-1 in
// EVERY word of physical row DEFECT_ROW -- the case a spare row exists for, and
// one that spends WORDS_PER_ROW spares if repair treats a word as a row.
// DEFECT2_ROW (-1 = off) plants a second one, at DEFECT2_BIT.
//
// WORDS_PER_ROW is baked in here: the wrapper passes the memory only
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS, like a real macro adapter.
// Used by tests/integration/test_repair_muxed_e2e.py (8 words, 4 per row: two
// physical rows, addresses 0-3 and 4-7).
module sram_spares_muxed #(
    parameter integer ADDR_WIDTH     = 3,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ROW     = 1,    // physical row 1: addresses 4..7
    parameter integer DEFECT_BIT     = 2,
    parameter integer DEFECT2_ROW    = -1,
    parameter integer DEFECT2_BIT    = 0
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [DATA_WIDTH-1:0]       din0,
    output logic [DATA_WIDTH-1:0]       dout0
);

    localparam integer WORDS_PER_ROW = 4;
    localparam integer COL_BITS      = 2;
    localparam integer PHYS_WORDS    = (1 << ADDR_WIDTH) + NUM_SPARE_ROWS * WORDS_PER_ROW;
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    logic [DATA_WIDTH-1:0]     mem [0:PHYS_WORDS-1];

    logic                      csb0_q;
    logic                      web0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    function automatic logic [DATA_WIDTH-1:0] stored(input integer addr, input logic [DATA_WIDTH-1:0] d);
        stored = d;
        if (DEFECT_ROW >= 0 && (addr >> COL_BITS) == DEFECT_ROW) stored = stored | DEFECT_MASK;
        if (DEFECT2_ROW >= 0 && (addr >> COL_BITS) == DEFECT2_ROW) stored = stored | DEFECT2_MASK;
    endfunction

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;

        if (!csb0 && !web0 && addr0 < PHYS_WORDS) begin
            mem[addr0] <= stored(addr0, din0);
        end

        if (!csb0_q && web0_q) begin
            dout0 <= (addr0_q < PHYS_WORDS) ? mem[addr0_q] : 'x;
        end
    end

endmodule
