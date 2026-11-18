`timescale 1ns/1ps
// sram_spares_muxed.v with BOTH physical rows broken (row 0 at bit 0, row 1 at
// bit 2), for the two-spare case: repairing it needs spare row 1, which starts
// at 2**ADDR_WIDTH + 1*WORDS_PER_ROW = 12, not at 2**ADDR_WIDTH + 1 = 9.
// The wrapper instantiates the memory by its memory_name, so this is a thin
// renamed module over the same model with the defect parameters set.
module sram_spares_muxed_2row #(
    parameter integer ADDR_WIDTH     = 3,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 2,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS)
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [DATA_WIDTH-1:0]       din0,
    output logic [DATA_WIDTH-1:0]       dout0
);

    sram_spares_muxed #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_SPARE_ROWS(NUM_SPARE_ROWS),
        .MEM_ADDR_WIDTH(MEM_ADDR_WIDTH),
        .DEFECT_ROW(1),
        .DEFECT_BIT(2),
        .DEFECT2_ROW(0),
        .DEFECT2_BIT(0)
    ) u_model (
        .clk0(clk0), .csb0(csb0), .web0(web0),
        .addr0(addr0), .din0(din0), .dout0(dout0)
    );

endmodule
