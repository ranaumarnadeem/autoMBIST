// openram_shim.sv
// Drop-in replacement for an OpenRAM-generated 1rw Verilog model.
// Matches the OpenRAM port naming (clk0/csb0/web0/wmask0/addr0/din0/dout0)
// and expands the per-byte write mask to the bit-level mask fault_ram uses.
//
// Instantiate this in place of the OpenRAM macro in the MBIST testbench,
// keeping the same instance connections. Set the three parameters to match
// the generated macro. Spare-row/col pins (spare_wen) are not modeled here;
// if your config uses spares, extend the address range instead.

`timescale 1ns/1ps

module openram_shim #(
  parameter int DATA_WIDTH = 32,
  parameter int ADDR_WIDTH = 8,
  parameter int NUM_WMASKS = 4          // DATA_WIDTH / write_size
)(
  input  logic                    clk0,
  input  logic                    csb0,
  input  logic                    web0,
  input  logic [NUM_WMASKS-1:0]   wmask0,
  input  logic [ADDR_WIDTH-1:0]   addr0,
  input  logic [DATA_WIDTH-1:0]   din0,
  output logic [DATA_WIDTH-1:0]   dout0
);

  localparam int GRP = DATA_WIDTH / NUM_WMASKS;

  logic [DATA_WIDTH-1:0] bitmask;
  always_comb
    for (int g = 0; g < NUM_WMASKS; g++)
      bitmask[g*GRP +: GRP] = {GRP{wmask0[g]}};

  fault_ram #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) u_core (
    .clk   (clk0),
    .csb   (csb0),
    .web   (web0),
    .wmask (bitmask),
    .addr  (addr0),
    .din   (din0),
    .dout  (dout0)
  );

endmodule
