`timescale 1ns/1ps
// A deliberately tiny SRAM (depth=4, width=4) for exercising the smallest
// sensible memory shape end-to-end -- edge cases in march-algorithm address
// wraparound and fault-mask depth that a large default memory would not
// stress. Active-low write enable (matches sram_model.sv's convention).

module sram_tiny #(
    parameter integer ADDR_WIDTH = 2,
    parameter integer DATA_WIDTH = 4
) (
    input  wire                  clk0,
    input  wire                  csb0,
    input  wire [ADDR_WIDTH-1:0] addr0,
    input  wire [DATA_WIDTH-1:0] din0,
    input  wire                  web0,
    output reg [DATA_WIDTH-1:0]  dout0
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);

    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    reg                  csb0_q;
    reg                  web0_q;
    reg [ADDR_WIDTH-1:0] addr0_q;

    always @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;

        if (!csb0 && !web0) begin
            mem[addr0] <= din0;
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
