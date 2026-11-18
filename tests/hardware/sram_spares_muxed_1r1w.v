`timescale 1ns/1ps
// The 1-read + 1-write port version of sram_spares_muxed.v: a COLUMN-MUXED
// spare-augmented memory with WORDS_PER_ROW = 2, the value OpenRAM picks for
// sky130_sram_1r1w_32b256w. Port 0 is read-only and registered, port 1 is
// write-only, sharing one array, like sram_spares_tiny_1r1w.v.
//
// Spare row i holds addresses 2**ADDR_WIDTH + i*WORDS_PER_ROW onwards.
// Addresses past the last spare row do not exist: writes to them are dropped
// and reads return X, so a remap that steers off the real array fails.
//
// The planted defect is a WORDLINE defect: bit DEFECT_BIT is stuck-at-1 in
// both words of physical row DEFECT_ROW (addresses 2 and 3 by default).
// Used by tests/integration/test_repair_muxed_e2e.py.
module sram_spares_muxed_1r1w #(
    parameter integer ADDR_WIDTH     = 3,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ROW     = 1,    // physical row 1: addresses 2..3
    parameter integer DEFECT_BIT     = 3
) (
    // Port 0: read-only.
    input  logic                      clk0,
    input  logic                      csb0,
    input  logic [MEM_ADDR_WIDTH-1:0] addr0,
    output logic [DATA_WIDTH-1:0]     dout0,

    // Port 1: write-only.
    input  logic                      clk1,
    input  logic                      csb1,
    input  logic                      web1,
    input  logic [MEM_ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0]     din1
);

    localparam integer WORDS_PER_ROW = 2;
    localparam integer COL_BITS      = 1;
    localparam integer PHYS_WORDS    = (1 << ADDR_WIDTH) + NUM_SPARE_ROWS * WORDS_PER_ROW;
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK = (1 << DEFECT_BIT);

    logic [DATA_WIDTH-1:0]     mem [0:PHYS_WORDS-1];

    logic                      csb0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        addr0_q <= addr0;

        if (!csb0_q) begin
            dout0 <= (addr0_q < PHYS_WORDS) ? mem[addr0_q] : 'x;
        end
    end

    always_ff @(posedge clk1) begin
        if (!csb1 && !web1 && addr1 < PHYS_WORDS) begin
            if (DEFECT_ROW >= 0 && (addr1 >> COL_BITS) == DEFECT_ROW) begin
                mem[addr1] <= din1 | DEFECT_MASK;
            end else begin
                mem[addr1] <= din1;
            end
        end
    end

endmodule
