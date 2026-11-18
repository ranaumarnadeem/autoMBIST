`timescale 1ns/1ps
// External row-repair address remap for the redundancy-repair (BIRA/BISR) flow.
//
// Purely combinational: it sits between the MBIST/functional address mux and the
// spare-augmented memory (rtl/sram_model_spares.sv), and substitutes a spare-row
// physical address for any logical address a loaded repair register marks faulty.
// This is the "external remap around a stock macro" architecture -- the memory
// itself has no repair pins; ALL steering is here, in standard-cell logic that
// synthesizes and hardens like any other combinational block.
//
// Repair config (driven later by BISR / by the tester): for each of the
// NUM_SPARE_ROWS spares, row_repair_en[i] enables spare i, and the i-th slice of
// faulty_row_addr gives a LOGICAL address in the row it replaces. No clock/reset:
// a future BISR load block registers the config UPSTREAM of these inputs without
// touching this module.
//
// WORDS_PER_ROW is the memory's column-mux factor (a power of two). A physical
// row holds WORDS_PER_ROW consecutive addresses: OpenRAM wires the low
// log2(WORDS_PER_ROW) address bits to the column decoder and the rest to the row
// decoder, and puts the spare rows after the last regular row. So spare row i
// starts at 2**ADDR_WIDTH + i*WORDS_PER_ROW. A spare replaces the WHOLE physical
// row: any address whose row bits match the slice is steered to spare row i,
// keeping its column bits. At the default of 1 (no muxing) the row is one word
// and spare i sits at 2**ADDR_WIDTH + i, as before this parameter existed.
module repair_remap_row #(
    parameter integer ADDR_WIDTH     = 10,   // logical (pre-repair) address width
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer WORDS_PER_ROW  = 1,    // column-mux factor, a power of two
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS * WORDS_PER_ROW)
) (
    input  logic [ADDR_WIDTH-1:0]                addr_in,
    output logic [MEM_ADDR_WIDTH-1:0]            addr_out,

    input  logic [NUM_SPARE_ROWS-1:0]            row_repair_en,
    input  logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0] faulty_row_addr
);

    localparam integer BASE_WORDS = (1 << ADDR_WIDTH);
    localparam integer COL_BITS   = $clog2(WORDS_PER_ROW);
    localparam logic [ADDR_WIDTH-1:0] COL_MASK = WORDS_PER_ROW - 1;

    always_comb begin
        // Default: pass the logical address straight through (auto zero-extended
        // to the physical width on assignment).
        addr_out = addr_in;
        for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
            if (row_repair_en[i] &&
                (addr_in >> COL_BITS) == (faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] >> COL_BITS)) begin
                // Steer to the same column of spare row i, past the last regular row.
                addr_out = BASE_WORDS + i * WORDS_PER_ROW + (addr_in & COL_MASK);
            end
        end
    end

endmodule
