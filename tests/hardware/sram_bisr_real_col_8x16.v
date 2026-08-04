`timescale 1ns/1ps
// sram_bisr_real_col_8x16.v -- thin compatibility shim wrapping a REAL
// OpenRAM-compiled SRAM macro WITH A SPARE COLUMN, so the generic BISR wrapper
// (wrapper_template.j2's redundancy memory instantiation) can drive it exactly
// as it drives the toy sram_spares_col_tiny.v/sram_model_spares.sv models:
// taking a LOGICAL ADDR_WIDTH/DATA_WIDTH + NUM_SPARE_ROWS/NUM_SPARE_COLS and
// deriving the physical port widths internally -- the exact same formulas
// repair/types.py::SpareGeometry.mem_addr_width and .mem_data_width use.
//
// The column sibling of sram_bisr_real_8x16.v, and NECESSARY for the same
// reason: the real OpenRAM-generated macro (sram_bisr_real_col_8x16_core,
// below) has NO NUM_SPARE_ROWS/NUM_SPARE_COLS parameters at all -- its physical
// ADDR_WIDTH=5 / DATA_WIDTH=9 are baked in permanently at compile time. Real
// silicon-representative macros do not expose runtime "how many spares" knobs;
// only our own toy behavioral models do, purely for cheap test-scenario
// parameterization.
//
// WHY THIS MACRO EXISTS SEPARATELY FROM sram_bisr_real_8x16: that one was
// compiled with num_spare_cols=0 and therefore has no spare_wen0 pin at all,
// so it cannot exercise column repair no matter how it is wrapped. This one was
// generated specifically for the Workstream M.1 column proof, via
// `scripts/synthesize_sram.sh --tech scn4m_subm --word-size 8 --num-words 16
// --num-rw-ports 1 --num-spare-rows 2 --num-spare-cols 1
// --output-name sram_bisr_real_col_8x16` (native SCMOS 0.35um, no PDK/magic
// required). The full artifact set (.gds/.lef/.lib/.sp/.html/.log) is gitignored
// under input/sram_bisr_real_col_8x16/ -- only the Verilog is needed for RTL sim.
//
// Worth noting what the core below independently confirms: its write block is
//     mem[addr0_reg][7:0] = din0_reg[7:0];
//     if (spare_wen0_reg) mem[addr0_reg][8] = din0_reg[8];
// -- a LOGICAL-word part-select plus a separately spare_wen-gated spare store.
// That is exactly the shape rtl/sram_model_spares.sv uses, which means the
// behavioral model's spare-column semantics match a real macro rather than
// merely being self-consistent. It also confirms the vendored OpenRAM
// no-wmask+spare-column write fix (OpenRAM/compiler/base/verilog.py) is live:
// an unfixed OpenRAM would have emitted [6:0] here, orphaning data bit 7.
//
// Fixed-geometry note: this shim only makes sense for the EXACT geometry
// sram_bisr_real_col_8x16_core was compiled for (word_size=8, num_words=16,
// num_spare_rows=2, num_spare_cols=1 -> MEM_ADDR_WIDTH=5, MEM_DATA_WIDTH=9).
// It cannot resize an already-generated real macro.

module sram_bisr_real_col_8x16 #(
    parameter integer ADDR_WIDTH     = 4,   // LOGICAL width, matches the wrapper's own
    parameter integer DATA_WIDTH     = 8,   // LOGICAL word width (no spare columns)
    parameter integer NUM_SPARE_ROWS = 2,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS
) (
    input  logic                      clk0,
    input  logic                      csb0,
    input  logic                      web0,
    input  logic [MEM_ADDR_WIDTH-1:0] addr0,
    input  logic [MEM_DATA_WIDTH-1:0] din0,
    output logic [MEM_DATA_WIDTH-1:0] dout0,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0
);

    sram_bisr_real_col_8x16_core #(
        .DATA_WIDTH (MEM_DATA_WIDTH),
        .ADDR_WIDTH (MEM_ADDR_WIDTH)
    ) u_core (
        .clk0       (clk0),
        .csb0       (csb0),
        .web0       (web0),
        .spare_wen0 (spare_wen0[0]),
        .addr0      (addr0),
        .din0       (din0),
        .dout0      (dout0)
    );

endmodule

// -----------------------------------------------------------------------
// sram_bisr_real_col_8x16_core -- UNMODIFIED OpenRAM-generated output (renamed
// from `sram_bisr_real_col_8x16` to `sram_bisr_real_col_8x16_core` so the shim
// above can own the name the wrapper/config actually instantiate).
// -----------------------------------------------------------------------
// OpenRAM SRAM model
// Words: 16
// Word size: 8

module sram_bisr_real_col_8x16_core(
`ifdef USE_POWER_PINS
    vdd,
    gnd,
`endif
// Port 0: RW
    clk0,csb0,web0,spare_wen0,addr0,din0,dout0
  );

  parameter DATA_WIDTH = 9 ;
  parameter ADDR_WIDTH = 5 ;
  parameter RAM_DEPTH = 1 << ADDR_WIDTH;
  // FIXME: This delay is arbitrary.
  parameter DELAY = 3 ;
  parameter VERBOSE = 1 ; //Set to 0 to only display warnings
  parameter T_HOLD = 1 ; //Delay to hold dout value after posedge. Value is arbitrary

`ifdef USE_POWER_PINS
    inout vdd;
    inout gnd;
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
