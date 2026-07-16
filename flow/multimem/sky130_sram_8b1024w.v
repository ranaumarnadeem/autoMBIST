// PATCHED (openMBIST): explicit timescale added -- the OpenRAM-emitted model has
// none, so its #(T_HOLD)/#(DELAY) delays inherit the simulator default unit and
// dout never updates within an ns-scale clock period.
`timescale 1ns/1ps
// OpenRAM SRAM model
// Words: 1024
// Word size: 8

module sky130_sram_8b1024w(
`ifdef USE_POWER_PINS
    vccd1,
    vssd1,
`endif
// Port 0: RW
    clk0,csb0,web0,spare_wen0,addr0,din0,dout0
  );

  parameter DATA_WIDTH = 9 ;
  parameter ADDR_WIDTH = 11 ;
  parameter RAM_DEPTH = 1 << ADDR_WIDTH;
  // FIXME: This delay is arbitrary.
  parameter DELAY = 3 ;
  parameter VERBOSE = 1 ; //Set to 0 to only display warnings
  parameter T_HOLD = 1 ; //Delay to hold dout value after posedge. Value is arbitrary

`ifdef USE_POWER_PINS
    inout vccd1;
    inout vssd1;
`endif
  input  clk0; // clock
  input   csb0; // active low chip select
  input  web0; // active low write control
  input [ADDR_WIDTH-1:0]  addr0;
  input           spare_wen0; // spare mask
  input [DATA_WIDTH-1:0]  din0;
  output [DATA_WIDTH-1:0] dout0;

  reg [DATA_WIDTH-1:0]    mem [0:RAM_DEPTH-1];

  reg  csb0_reg;
  reg  web0_reg;
  reg spare_wen0_reg;
  reg [ADDR_WIDTH-1:0]  addr0_reg;
  reg [DATA_WIDTH-1:0]  din0_reg;
  reg [DATA_WIDTH-1:0]  dout0;

  // All inputs are registers
  always @(posedge clk0)
  begin
    csb0_reg = csb0;
    web0_reg = web0;
    spare_wen0_reg = spare_wen0;
    addr0_reg = addr0;
    din0_reg = din0;
    #(T_HOLD) dout0 = 8'bx;
    if ( !csb0_reg && web0_reg && VERBOSE )
      $display($time," Reading %m addr0=%b dout0=%b",addr0_reg,mem[addr0_reg]);
    if ( !csb0_reg && !web0_reg && VERBOSE )
      $display($time," Writing %m addr0=%b din0=%b",addr0_reg,din0_reg);
  end


  // Memory Write Block Port 0
  // Write Operation : When web0 = 0, csb0 = 0
  always @ (negedge clk0)
  begin : MEM_WRITE0
    if ( !csb0_reg && !web0_reg ) begin
        // PATCHED (openMBIST): OpenRAM's no-wmask writer emitted [6:0], orphaning
        // data bit 7 (the spare column is [8], gated below) -- root cause fixed in
        // vendored OpenRAM/compiler/base/verilog.py (base write = word_size-1 : 0).
        mem[addr0_reg][7:0] = din0_reg[7:0];
        if (spare_wen0_reg)
                mem[addr0_reg][8] = din0_reg[8];
    end
  end

  // Memory Read Block Port 0
  // Read Operation : When web0 = 1, csb0 = 0
  always @ (negedge clk0)
  begin : MEM_READ0
    if (!csb0_reg && web0_reg)
       dout0 <= #(DELAY) mem[addr0_reg];
  end

endmodule
