`timescale 1ns/1ps
// External column-repair bit-steer for the redundancy-repair (BIRA/BISR) flow.
//
// Purely combinational sibling of rtl/repair_remap_row.sv: it sits between the
// MBIST/functional data path and the spare-augmented memory, steering the data
// bits a loaded repair register marks faulty onto spare COLUMNS. Same "external
// remap around a stock macro" architecture -- the memory itself has no repair
// logic; ALL steering is here, in standard-cell logic that synthesizes and
// hardens like any other combinational block.
//
// Convention (matches OpenRAM's spare-column interface exactly):
//   * Spare column k lives at physical bit DATA_WIDTH + k. A real macro's
//     din0/dout0 are (word_size + num_spare_cols) wide -- e.g. DATA_WIDTH=33 on
//     flow/multimem/sky130_sram_32b512w.v, with the spare at bit 32.
//   * WRITE: the logical bits pass through untouched, and each enabled spare
//     column carries a COPY of the bit it replaces. The faulty lane is still
//     written -- a stock macro has no per-bit write disable -- it is simply
//     never read again. This is why column repair needs no address involvement
//     at all, and why it composes with row repair for free (a spare row spans
//     the full array width, spare columns included).
//   * READ: dout_in[DATA_WIDTH + k] is substituted for the faulty lane.
//   * spare_wen is active-high and only meaningful during a write: the macro
//     already ANDs it with its own (!csb && !web) internally, so this module
//     needs no csb/we input.
//
// spare_wen is driven from col_repair_en (NOT tied high), so that with repair
// disabled this module is inert -- the same "unused feature changes nothing"
// invariant repair_remap_row.sv holds by passing addr_in straight through.
// Consequence, deliberate and tested: a spare lane holds X/stale data until the
// first write to that address AFTER the signature is applied. Every flow here
// writes before it reads (the functional fail scan and the BIST both rewrite
// everything), so this is bounded -- but a caller that enables repair midway
// through and reads without rewriting will see X on the repaired lane.
//
// Repair config (driven by BISR / by the tester): for each of the
// NUM_SPARE_COLS spares, col_repair_en[k] enables spare k, and the k-th slice
// of faulty_bit gives the LOGICAL bit index it replaces -- exactly mirroring
// repair_remap_row.sv's row_repair_en/faulty_row_addr packing, and exactly what
// src/autombist/repair/bisr.py::encode_repair produces. No clock/reset.
//
// Valid only for words_per_row == 1 (no column muxing). Under column muxing
// OpenRAM shares ONE spare-column set per PHYSICAL row across the muxed words
// (OpenRAM's functional.py:71-77), which this global bit-lane model does not
// express -- generator.py rejects redundancy.words_per_row != 1 for that reason.
module repair_remap_col #(
    parameter integer DATA_WIDTH     = 32,   // logical (pre-repair) word width
    parameter integer NUM_SPARE_COLS = 1,
    // Width of each packed faulty_bit slice. A spare column is never itself
    // "faulty", so this indexes the LOGICAL word only -- the same reasoning
    // repair/types.py::SpareGeometry.bit_index_width documents. The
    // (DATA_WIDTH > 1) guard mirrors that property's max(1, ...): $clog2(1) is
    // 0, which would make faulty_bit a [-1:0] zero-width port.
    parameter integer BIT_IDX_WIDTH  = (DATA_WIDTH > 1) ? $clog2(DATA_WIDTH) : 1,
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS
) (
    input  logic [DATA_WIDTH-1:0]                   din_in,
    output logic [MEM_DATA_WIDTH-1:0]               din_out,

    input  logic [MEM_DATA_WIDTH-1:0]               dout_in,
    output logic [DATA_WIDTH-1:0]                   dout_out,

    output logic [NUM_SPARE_COLS-1:0]               spare_wen,

    input  logic [NUM_SPARE_COLS-1:0]               col_repair_en,
    input  logic [NUM_SPARE_COLS*BIT_IDX_WIDTH-1:0] faulty_bit
);

    always_comb begin
        // Write path: logical bits straight through; each spare lane mirrors the
        // bit it replaces. Driven unconditionally -- spare_wen below is what
        // actually gates the store, exactly as on a real macro.
        din_out = '0;
        din_out[DATA_WIDTH-1:0] = din_in;
        for (int k = 0; k < NUM_SPARE_COLS; k++) begin
            din_out[DATA_WIDTH + k] =
                din_in[faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH]];
        end

        // Read path: an explicit per-bit compare+mux rather than a
        // variable-index LHS write, so that a faulty_bit slice pointing outside
        // [0, DATA_WIDTH) is INERT rather than silently dropping an assignment.
        for (int b = 0; b < DATA_WIDTH; b++) begin
            dout_out[b] = dout_in[b];
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (col_repair_en[k] &&
                    faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] == b) begin
                    dout_out[b] = dout_in[DATA_WIDTH + k];
                end
            end
        end
    end

    assign spare_wen = col_repair_en;

endmodule
