`timescale 1ns/1ps
// External column-repair bit-steer for the redundancy-repair (BIRA/BISR) flow.
//
// Sibling of rtl/repair_remap_row.sv: it sits between the MBIST/functional
// data path and the spare-augmented memory, steering the data bits a loaded
// repair register marks faulty onto spare COLUMNS. Same "external remap around
// a stock macro" architecture -- the memory itself has no repair logic; ALL
// steering is here, in standard-cell logic that synthesizes and hardens like
// any other block.
//
// Convention (matches OpenRAM's spare-column interface exactly):
//   * Spare column k lives at physical bit DATA_WIDTH + k. A real macro's
//     din0/dout0 are (word_size + num_spare_cols) wide -- e.g. DATA_WIDTH=33 on
//     flow/multimem/sky130_sram_32b512w.v, with the spare at bit 32.
//   * WRITE: the logical bits pass through untouched, and each enabled spare
//     column carries a COPY of the bit it replaces. The faulty lane is still
//     written -- a stock macro has no per-bit write disable -- it is simply
//     never read again. Composes with row repair for free (a spare row spans
//     the full array width, spare columns included).
//   * READ: dout_in[DATA_WIDTH + k] is substituted for the faulty lane.
//   * spare_wen is active-high and only meaningful during a write: the macro
//     already ANDs it with its own (!csb && !web) internally, so this module
//     needs no csb/we input for writes.
//
// Column muxing (WORDS_PER_ROW > 1). A physical row then holds WORDS_PER_ROW
// words, their bits interleaved, and the low log2(WORDS_PER_ROW) address bits
// pick the word. OpenRAM routes the spare columns AROUND the column mux
// (port_data.py), so a spare column has ONE cell per physical row, shared by
// every word in it: it can replace one PHYSICAL column -- bit b of the words
// whose column bits are w -- and nothing else. Each faulty_bit slice then
// names that physical column, b * WORDS_PER_ROW + w (SpareGeometry.col_of),
// and the steer applies only to word column w:
//   * spare_wen[k] is raised only when the write's column bits (wr_col) are
//     w. Raising it for another word of the row would overwrite the one shared
//     spare cell with that word's bit b.
//   * the read substitution applies only when the column bits of the read
//     that produced dout_in are w. dout_in lags the address, so rd_col is
//     captured when the read is issued (rd_issue) and delayed READ_LATENCY
//     more cycles, as the memory delays its data: READ_LATENCY 0 for a memory
//     that registers the address on a clock edge and drives dout before the
//     next one (OpenRAM), 1 for one that drives dout on that next edge and
//     holds it (this project's fixtures) -- the controller's read_latency.
//     Each stage only advances behind a read, so a held dout keeps its column
//     through later writes.
// At WORDS_PER_ROW == 1 every match is true, clk/wr_col/rd_col/rd_issue are
// unused and the module is combinational, exactly as before muxing existed.
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
// of faulty_bit gives the column it replaces -- exactly mirroring
// repair_remap_row.sv's row_repair_en/faulty_row_addr packing, and exactly what
// src/autombist/repair/bisr.py::encode_repair produces.
module repair_remap_col #(
    parameter integer DATA_WIDTH     = 32,   // logical (pre-repair) word width
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer WORDS_PER_ROW  = 1,    // column-mux factor, a power of two
    parameter integer READ_LATENCY   = 1,    // used only when WORDS_PER_ROW > 1
    // Width of each packed faulty_bit slice: a regular physical column
    // (DATA_WIDTH * WORDS_PER_ROW of them; a spare column is never itself
    // "faulty") -- the same reasoning repair/types.py::SpareGeometry.
    // bit_index_width documents. The (> 1) guard mirrors that property's
    // max(1, ...): $clog2(1) is 0, which would make faulty_bit a [-1:0]
    // zero-width port.
    parameter integer BIT_IDX_WIDTH  = (DATA_WIDTH * WORDS_PER_ROW > 1) ? $clog2(DATA_WIDTH * WORDS_PER_ROW) : 1,
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer COL_SEL_WIDTH  = (WORDS_PER_ROW > 1) ? $clog2(WORDS_PER_ROW) : 1
) (
    input  logic [DATA_WIDTH-1:0]                   din_in,
    output logic [MEM_DATA_WIDTH-1:0]               din_out,

    input  logic [MEM_DATA_WIDTH-1:0]               dout_in,
    output logic [DATA_WIDTH-1:0]                   dout_out,

    output logic [NUM_SPARE_COLS-1:0]               spare_wen,

    input  logic [NUM_SPARE_COLS-1:0]               col_repair_en,
    input  logic [NUM_SPARE_COLS*BIT_IDX_WIDTH-1:0] faulty_bit,

    // Column muxing only (WORDS_PER_ROW > 1); unused, and left unconnected by
    // the generated wrapper, at 1.
    input  logic                                    clk,
    input  logic [COL_SEL_WIDTH-1:0]                wr_col,    // column bits of this cycle's write
    input  logic [COL_SEL_WIDTH-1:0]                rd_col,    // column bits of this cycle's read
    input  logic                                    rd_issue   // a read is issued this cycle
);

    localparam integer COL_BITS = $clog2(WORDS_PER_ROW);

    // The column bits of the read whose data dout_in carries now.
    logic [COL_SEL_WIDTH-1:0] rd_sel;

    generate
        if (WORDS_PER_ROW > 1) begin : g_rd_pipe
            // Stage i is slice i (flat vectors: Yosys does not resolve a
            // packed 2-D array indexed inside a generate block).
            logic [(READ_LATENCY+1)*COL_SEL_WIDTH-1:0] rd_col_q;
            logic [READ_LATENCY:0]                     rd_v_q;
            always_ff @(posedge clk) begin
                rd_v_q[0] <= rd_issue;
                if (rd_issue) rd_col_q[0 +: COL_SEL_WIDTH] <= rd_col;
                for (int i = 1; i <= READ_LATENCY; i++) begin
                    rd_v_q[i] <= rd_v_q[i-1];
                    if (rd_v_q[i-1]) rd_col_q[i*COL_SEL_WIDTH +: COL_SEL_WIDTH] <= rd_col_q[(i-1)*COL_SEL_WIDTH +: COL_SEL_WIDTH];
                end
            end
            assign rd_sel = rd_col_q[READ_LATENCY*COL_SEL_WIDTH +: COL_SEL_WIDTH];
        end else begin : g_no_rd_pipe
            assign rd_sel = '0;
        end
    endgenerate

    always_comb begin
        // Write path: logical bits straight through; each spare lane mirrors the
        // bit it replaces. Driven unconditionally -- spare_wen below is what
        // actually gates the store, exactly as on a real macro.
        din_out = '0;
        din_out[DATA_WIDTH-1:0] = din_in;
        for (int k = 0; k < NUM_SPARE_COLS; k++) begin
            din_out[DATA_WIDTH + k] =
                din_in[faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] >> COL_BITS];
            spare_wen[k] = col_repair_en[k] &&
                ((WORDS_PER_ROW == 1) ||
                 wr_col == faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] % WORDS_PER_ROW);
        end

        // Read path: an explicit per-bit compare+mux rather than a
        // variable-index LHS write, so that a faulty_bit slice pointing outside
        // the array is INERT rather than silently dropping an assignment.
        for (int b = 0; b < DATA_WIDTH; b++) begin
            dout_out[b] = dout_in[b];
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (col_repair_en[k] &&
                    (faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] >> COL_BITS) == b &&
                    ((WORDS_PER_ROW == 1) ||
                     rd_sel == faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] % WORDS_PER_ROW)) begin
                    dout_out[b] = dout_in[DATA_WIDTH + k];
                end
            end
        end
    end

endmodule
