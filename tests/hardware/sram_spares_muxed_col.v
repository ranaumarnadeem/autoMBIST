`timescale 1ns/1ps
// A COLUMN-MUXED memory with spare rows AND spare columns, laid out the way
// OpenRAM lays one out:
//   * WORDS_PER_ROW consecutive addresses share a physical row (the low address
//     bits pick the word, the words' bits interleaved), and the spare rows
//     follow the last regular row: spare row i holds addresses
//     2**ADDR_WIDTH + i*WORDS_PER_ROW onwards.
//   * the spare columns bypass the column mux, so each has ONE cell per
//     physical row (spare rows included), SHARED by every word of the row:
//     a write with spare_wen0[k] stores din0[DATA_WIDTH + k] in it whichever
//     word it addresses, and a read of any word of the row returns it on
//     dout0[DATA_WIDTH + k]. (OpenRAM's own behavioral .v gives each word its
//     own spare bit, which would hide a remap that writes the spare from the
//     wrong word.)
//
// The planted defect is a BITLINE defect: bit DEFECT_BIT of word column
// DEFECT_WCOL is stuck-at-1 in EVERY physical row, spare rows included -- one
// physical column, the case a spare column exists for. DEFECT_ROW (-1 = off)
// adds a wordline defect: bit DEFECT_ROW_BIT stuck-at-1 in every word of that
// physical row.
//
// READ_LATENCY 1 (this module): the address is registered on a clock edge and
// dout0 is driven on the next one and held until the next read, like
// sram_spares_muxed.v. READ_LATENCY 0 (sram_spares_muxed_col_l0.v): the write
// and the read are done on the falling edge after the address is registered,
// as an OpenRAM macro does, and dout0 goes X at the next rising edge.
//
// WORDS_PER_ROW and the defect are baked in: the wrapper passes the memory only
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS, like a real macro adapter.
// Used by tests/integration/test_repair_muxed_col_e2e.py (8 words, 4 per row:
// two physical rows, addresses 0-3 and 4-7, plus one spare row at 8-11).
module sram_spares_muxed_col #(
    parameter integer ADDR_WIDTH     = 3,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS * 4),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer READ_LATENCY   = 1,
    parameter integer DEFECT_WCOL    = 2,    // word column 2 (addresses 2, 6, 10) ...
    parameter integer DEFECT_BIT     = 1,    // ... bit 1: physical column 1*4 + 2 = 6
    parameter integer DEFECT_ROW     = -1,
    parameter integer DEFECT_ROW_BIT = 3
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [MEM_DATA_WIDTH-1:0]   din0,
    output logic [MEM_DATA_WIDTH-1:0]   dout0,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0
);

    localparam integer WORDS_PER_ROW = 4;
    localparam integer COL_BITS      = 2;
    localparam integer PHYS_WORDS    = (1 << ADDR_WIDTH) + NUM_SPARE_ROWS * WORDS_PER_ROW;
    localparam integer PHYS_ROWS     = PHYS_WORDS / WORDS_PER_ROW;

    logic [DATA_WIDTH-1:0] mem   [0:PHYS_WORDS-1];
    logic [NUM_SPARE_COLS-1:0] spare [0:PHYS_ROWS-1];  // one cell per physical row per spare

    logic                      csb0_q;
    logic                      web0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;
    logic [MEM_DATA_WIDTH-1:0] din0_q;
    logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0_q;

    function automatic logic [DATA_WIDTH-1:0] stored(input integer addr, input logic [DATA_WIDTH-1:0] d);
        stored = d;
        if (addr % WORDS_PER_ROW == DEFECT_WCOL) stored[DEFECT_BIT] = 1'b1;
        if (DEFECT_ROW >= 0 && (addr >> COL_BITS) == DEFECT_ROW) stored[DEFECT_ROW_BIT] = 1'b1;
    endfunction

    task automatic do_write(input integer addr, input logic [MEM_DATA_WIDTH-1:0] d,
                            input logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] wen);
        if (addr < PHYS_WORDS) begin
            mem[addr] <= stored(addr, d[DATA_WIDTH-1:0]);
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (wen[k]) spare[addr >> COL_BITS][k] <= d[DATA_WIDTH + k];
            end
        end
    endtask

    function automatic logic [MEM_DATA_WIDTH-1:0] word(input integer addr);
        word = (addr < PHYS_WORDS) ? {spare[addr >> COL_BITS], mem[addr]} : 'x;
    endfunction

    generate
        if (READ_LATENCY == 0) begin : g_openram
            always_ff @(posedge clk0) begin
                csb0_q       <= csb0;
                web0_q       <= web0;
                addr0_q      <= addr0;
                din0_q       <= din0;
                spare_wen0_q <= spare_wen0;
                dout0        <= 'x;
            end
            always_ff @(negedge clk0) begin
                if (!csb0_q && !web0_q) do_write(addr0_q, din0_q, spare_wen0_q);
                if (!csb0_q && web0_q)  dout0 <= word(addr0_q);
            end
        end else begin : g_registered
            always_ff @(posedge clk0) begin
                csb0_q  <= csb0;
                web0_q  <= web0;
                addr0_q <= addr0;
                if (!csb0 && !web0) do_write(addr0, din0, spare_wen0);
                if (!csb0_q && web0_q) dout0 <= word(addr0_q);
            end
        end
    endgenerate

endmodule
