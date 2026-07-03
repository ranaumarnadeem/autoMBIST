// openram_shim_mp.sv
// 2-port sibling of openram_shim.sv -- NOT a modification of that file.
// Drop-in replacement for an OpenRAM-generated 2-port (2RW) Verilog model.
// Matches the OpenRAM port naming per port (clk0/csb0/web0/wmask0/addr0/
// din0/dout0, clk1/csb1/web1/wmask1/addr1/din1/dout1) and expands each
// port's per-byte write mask to the bit-level mask fault_ram uses.
//
// CRITICAL: this wraps EXACTLY ONE fault_ram core, rendered with
// num_ports=2 (see fault_ram_template.sv.j2 / fault_ram_gen.render_fault_ram
// (num_ports=2)) -- ONE shared fault-record queue/array underneath both
// port buses. This is what makes cross-port coupling faults meaningful (an
// aggressor write on port 1 disturbing a victim read on port 0); wrapping
// two independent fault_ram cores here would silently defeat that entirely
// by giving each port its own memory array with no shared fault state.
//
// Instantiate this in place of the OpenRAM macro in a 2-port MBIST
// testbench, keeping the same instance connections. Set the three
// parameters to match the generated macro. Spare-row/col pins (spare_wen)
// are not modeled here; if your config uses spares, extend the address
// range instead.

`timescale 1ns/1ps

module openram_shim_mp #(
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
  output logic [DATA_WIDTH-1:0]   dout0,

  input  logic                    clk1,
  input  logic                    csb1,
  input  logic                    web1,
  input  logic [NUM_WMASKS-1:0]   wmask1,
  input  logic [ADDR_WIDTH-1:0]   addr1,
  input  logic [DATA_WIDTH-1:0]   din1,
  output logic [DATA_WIDTH-1:0]   dout1
);

  localparam int GRP = DATA_WIDTH / NUM_WMASKS;

  logic [DATA_WIDTH-1:0] bitmask0;
  always_comb
    for (int g = 0; g < NUM_WMASKS; g++)
      bitmask0[g*GRP +: GRP] = {GRP{wmask0[g]}};

  logic [DATA_WIDTH-1:0] bitmask1;
  always_comb
    for (int g = 0; g < NUM_WMASKS; g++)
      bitmask1[g*GRP +: GRP] = {GRP{wmask1[g]}};

  // Exactly ONE fault_ram core (num_ports=2 shape), shared by both port
  // buses -- see the module-header CRITICAL note above.
  fault_ram #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) u_core (
    .clk0   (clk0),
    .csb0   (csb0),
    .web0   (web0),
    .wmask0 (bitmask0),
    .addr0  (addr0),
    .din0   (din0),
    .dout0  (dout0),

    .clk1   (clk1),
    .csb1   (csb1),
    .web1   (web1),
    .wmask1 (bitmask1),
    .addr1  (addr1),
    .din1   (din1),
    .dout1  (dout1)
  );

endmodule
