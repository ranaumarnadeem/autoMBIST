`timescale 1ns/1ps
// sram_spares_muxed_col.v plus a WORDLINE defect: bit 3 stuck-at-1 in every
// word of physical row 0 (addresses 0-3), on top of the bitline defect (bit 1
// of word column 2, every physical row). One spare row takes row 0 and one
// spare column takes physical column 6, including the spare row's own cell on
// that bitline. Pass both files as the memory model.
module sram_spares_muxed_col_row #(
    parameter integer ADDR_WIDTH     = 3,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS * 4),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [MEM_DATA_WIDTH-1:0]   din0,
    output logic [MEM_DATA_WIDTH-1:0]   dout0,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0
);
    sram_spares_muxed_col #(
        .ADDR_WIDTH(ADDR_WIDTH), .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_ROWS(NUM_SPARE_ROWS), .NUM_SPARE_COLS(NUM_SPARE_COLS),
        .MEM_ADDR_WIDTH(MEM_ADDR_WIDTH), .DEFECT_ROW(0), .DEFECT_ROW_BIT(3)
    ) u_mem (
        .clk0(clk0), .csb0(csb0), .web0(web0), .addr0(addr0),
        .din0(din0), .dout0(dout0), .spare_wen0(spare_wen0)
    );
endmodule
