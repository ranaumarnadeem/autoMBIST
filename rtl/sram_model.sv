`timescale 1ns/1ps

module sram_model #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer DATA_WIDTH = 32
) (
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic                  web0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    input  logic [DATA_WIDTH-1:0] din0,
    output logic [DATA_WIDTH-1:0] dout0
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);

    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    logic                  csb0_q;
    logic                  web0_q;
    logic [ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
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
