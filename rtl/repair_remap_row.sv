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
// faulty_row_addr gives the LOGICAL address it replaces. On a match, the access
// is steered to physical address (2**ADDR_WIDTH + i) -- the top addresses where
// the spare rows live (mirroring OpenRAM's addressable spares). No clock/reset:
// a future BISR load block registers the config UPSTREAM of these inputs without
// touching this module.
module repair_remap_row #(
    parameter integer ADDR_WIDTH     = 10,   // logical (pre-repair) address width
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS)
) (
    input  logic [ADDR_WIDTH-1:0]                addr_in,
    output logic [MEM_ADDR_WIDTH-1:0]            addr_out,

    input  logic [NUM_SPARE_ROWS-1:0]            row_repair_en,
    input  logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0] faulty_row_addr
);

    localparam integer BASE_WORDS = (1 << ADDR_WIDTH);

    always_comb begin
        // Default: pass the logical address straight through (auto zero-extended
        // to the physical width on assignment).
        addr_out = addr_in;
        for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
            if (row_repair_en[i] &&
                addr_in == faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH]) begin
                // Steer to spare row i -- it lives at the top of the address space.
                addr_out = BASE_WORDS + i;
            end
        end
    end

endmodule
