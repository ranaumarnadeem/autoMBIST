`timescale 1ns/1ps
// The 2-read/write port version of sram_spares_muxed_col.v: a COLUMN-MUXED
// memory with spare rows and spare columns, WORDS_PER_ROW = 2. Both ports are
// full read/write (registered reads, writes on the clock edge), each with its
// own spare_wen, sharing one array.
//
// The spare columns bypass the column mux, so each has ONE cell per physical
// row (spare rows included), SHARED by the row's words AND by both ports: a
// write from either port with its spare_wen[k] stores that port's
// din[DATA_WIDTH + k] in it whichever word it addresses, and a read of either
// word of the row returns it on dout[DATA_WIDTH + k].
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
module sram_spares_muxed_col_2rw #(
    parameter integer ADDR_WIDTH     = 3,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS * 2),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT_WCOL    = 1,
    parameter integer DEFECT_BIT     = 2
) (
    input  logic                      clk0,
    input  logic                      csb0,
    input  logic                      web0,
    input  logic [MEM_ADDR_WIDTH-1:0] addr0,
    input  logic [MEM_DATA_WIDTH-1:0] din0,
    output logic [MEM_DATA_WIDTH-1:0] dout0,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0,

    input  logic                      clk1,
    input  logic                      csb1,
    input  logic                      web1,
    input  logic [MEM_ADDR_WIDTH-1:0] addr1,
    input  logic [MEM_DATA_WIDTH-1:0] din1,
    output logic [MEM_DATA_WIDTH-1:0] dout1,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen1
);

    localparam integer WORDS_PER_ROW = 2;
    localparam integer COL_BITS      = 1;
    localparam integer PHYS_WORDS    = (1 << ADDR_WIDTH) + NUM_SPARE_ROWS * WORDS_PER_ROW;
    localparam integer PHYS_ROWS     = PHYS_WORDS / WORDS_PER_ROW;

    logic [DATA_WIDTH-1:0]     mem   [0:PHYS_WORDS-1];
    logic [NUM_SPARE_COLS-1:0] spare [0:PHYS_ROWS-1];  // one cell per physical row per spare

    function automatic logic [DATA_WIDTH-1:0] stored(input integer addr, input logic [DATA_WIDTH-1:0] d);
        stored = d | ((addr % WORDS_PER_ROW == DEFECT_WCOL) ? (1 << DEFECT_BIT) : 0);
    endfunction

    function automatic logic [MEM_DATA_WIDTH-1:0] word(input integer addr);
        word = (addr < PHYS_WORDS) ? {spare[addr >> COL_BITS], mem[addr]} : 'x;
    endfunction

    logic                      csb0_q, web0_q, csb1_q, web1_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q, addr1_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;
        if (!csb0 && !web0 && addr0 < PHYS_WORDS) begin
            mem[addr0] <= stored(addr0, din0[DATA_WIDTH-1:0]);
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen0[k]) spare[addr0 >> COL_BITS][k] <= din0[DATA_WIDTH + k];
            end
        end
        if (!csb0_q && web0_q) dout0 <= word(addr0_q);
    end

    always_ff @(posedge clk1) begin
        csb1_q  <= csb1;
        web1_q  <= web1;
        addr1_q <= addr1;
        if (!csb1 && !web1 && addr1 < PHYS_WORDS) begin
            mem[addr1] <= stored(addr1, din1[DATA_WIDTH-1:0]);
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen1[k]) spare[addr1 >> COL_BITS][k] <= din1[DATA_WIDTH + k];
            end
        end
        if (!csb1_q && web1_q) dout1 <= word(addr1_q);
    end

endmodule
