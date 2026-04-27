`timescale 1ns/1ps

module sram_1rw #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer DATA_WIDTH = 32
) (
    input  wire                  clk0,
    input  wire                  csb0,
    input  wire [ADDR_WIDTH-1:0] addr0,
    input  wire [DATA_WIDTH-1:0] din0,
    input  wire                  we0,
    output reg [DATA_WIDTH-1:0]  dout0
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);

    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    reg                  csb0_q;
    reg                  we0_q;
    reg [ADDR_WIDTH-1:0] addr0_q;

    always @(posedge clk0) begin
        csb0_q  <= csb0;
        we0_q   <= we0;
        addr0_q <= addr0;

        if (!csb0 && !we0) begin
            mem[addr0] <= din0;
        end

        if (!csb0_q && we0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
