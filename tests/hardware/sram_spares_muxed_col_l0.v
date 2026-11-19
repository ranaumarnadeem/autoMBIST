`timescale 1ns/1ps
// sram_spares_muxed_col.v with OpenRAM's read timing (READ_LATENCY 0): the
// address is registered on the rising edge, the write and the read are done on
// the falling edge, and dout0 goes X at the next rising edge. Same layout and
// defect; see that file's header. Pass both files as the memory model.
module sram_spares_muxed_col_l0 #(
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
        .MEM_ADDR_WIDTH(MEM_ADDR_WIDTH), .READ_LATENCY(0)
    ) u_mem (
        .clk0(clk0), .csb0(csb0), .web0(web0), .addr0(addr0),
        .din0(din0), .dout0(dout0), .spare_wen0(spare_wen0)
    );
endmodule
