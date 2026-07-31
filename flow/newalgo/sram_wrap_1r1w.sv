`timescale 1ns/1ps
// Real-macro adapter for the march-1r1w self-repair hardening target: same
// substitution trick as sram_wrap_x.sv/sram_wrap_mp.sv (this module's name
// matches the `memory_name` the generated selfrepair_1r1w wrapper expects to
// instantiate, so it stands in for the toy sram_model_1r1w.sv fixture) --
// wrapping sky130_sram_1r1w_32b256w, a REAL genuinely-dual-port OpenRAM
// macro (num_r_ports=1, num_w_ports=1, num_spare_rows=2; see
// input/sky130_sram_1r1w_32b256w/sky130_sram_1r1w_32b256w.py).
//
// The wrapper drives a single-bit active-low write-enable (`webB`) into the
// write-only port, but that port has no such pin -- OpenRAM gives write-only
// ports a wmask bus instead (there's no "selected but not writing" case to
// express otherwise). wmask0=4'h0 when webB is deasserted reproduces the
// same "chip selected, no write happens" behavior a `we` pin would give.
module sram_wrap_1r1w #(parameter integer ADDR_WIDTH=8, DATA_WIDTH=32, NUM_SPARE_ROWS=2,
    parameter integer MEM_ADDR_WIDTH=$clog2((1<<ADDR_WIDTH)+NUM_SPARE_ROWS))
  (input logic clkA,csbA, input logic [MEM_ADDR_WIDTH-1:0] addrA, output logic [DATA_WIDTH-1:0] doutA,
   input logic clkB,csbB,webB, input logic [MEM_ADDR_WIDTH-1:0] addrB, input logic [DATA_WIDTH-1:0] dinB);
  logic [DATA_WIDTH:0] md;
  sky130_sram_1r1w_32b256w u_macro (
    .clk0(clkB),.csb0(csbB),.wmask0(webB ? 4'h0 : 4'hF),.spare_wen0(1'b0),.addr0(addrB),.din0({1'b0,dinB}),
    .clk1(clkA),.csb1(csbA),.addr1(addrA),.dout1(md));
  assign doutA = md[DATA_WIDTH-1:0];
endmodule
