`timescale 1ns/1ps
module sram_wrap_a #(parameter integer ADDR_WIDTH=8, DATA_WIDTH=32, NUM_SPARE_ROWS=1,
    parameter integer MEM_ADDR_WIDTH=$clog2((1<<ADDR_WIDTH)+NUM_SPARE_ROWS))
  (input logic clk0,csb0,web0, input logic [MEM_ADDR_WIDTH-1:0] addr0,
   input logic [DATA_WIDTH-1:0] din0, output logic [DATA_WIDTH-1:0] dout0);
  logic [DATA_WIDTH:0] md;
  sky130_sram_32b256w u_macro (.clk0(clk0),.csb0(csb0),.web0(web0),.wmask0(4'hF),
    .addr0(addr0),.din0({1'b0,din0}),.dout0(md),.spare_wen0(1'b0));
  assign dout0 = md[DATA_WIDTH-1:0];
endmodule
