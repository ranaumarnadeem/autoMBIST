`timescale 1ns/1ps
// Spare-column note: the real macro below is compiled with ONE spare column,
// so its data bus is DATA_WIDTH+1 whether or not the spare is used. Set
// NUM_SPARE_COLS=1 to surface it -- din0/dout0 widen and spare_wen0 becomes a
// real port that an external repair_remap_col can drive. At the default of 0
// this module is functionally identical to before the parameter existed: the
// top macro bit is zero-padded on write, dropped on read, and spare_wen0 is
// tied low exactly as it was hard-coded before.
module sram_wrap_c #(parameter integer ADDR_WIDTH=10, DATA_WIDTH=8, NUM_SPARE_ROWS=1,
    parameter integer NUM_SPARE_COLS=0,
    parameter integer MEM_ADDR_WIDTH=$clog2((1<<ADDR_WIDTH)+NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH=DATA_WIDTH+NUM_SPARE_COLS)
  (input logic clk0,csb0,web0, input logic [MEM_ADDR_WIDTH-1:0] addr0,
   input logic [MEM_DATA_WIDTH-1:0] din0, output logic [MEM_DATA_WIDTH-1:0] dout0,
   input logic [((NUM_SPARE_COLS>0)?NUM_SPARE_COLS:1)-1:0] spare_wen0);
  localparam integer MACRO_DATA_WIDTH = DATA_WIDTH + 1;  // macro has 1 spare col
  logic [MACRO_DATA_WIDTH-1:0] md_din, md_dout;
  always_comb begin
    md_din = '0;
    md_din[MEM_DATA_WIDTH-1:0] = din0;
  end
  sky130_sram_8b1024w u_macro (.clk0(clk0),.csb0(csb0),.web0(web0),
    .addr0(addr0),.din0(md_din),.dout0(md_dout),
    .spare_wen0((NUM_SPARE_COLS>0) ? spare_wen0[0] : 1'b0));
  assign dout0 = md_dout[MEM_DATA_WIDTH-1:0];
endmodule
