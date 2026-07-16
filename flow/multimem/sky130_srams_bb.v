// Port-only blackbox stubs for the three OpenRAM sky130 macros (synthesis view
// for LibreLane/yosys -- do NOT include in simulation builds alongside the
// behavioral sky130_sram_*.v models: the module names collide by design).
// Pinouts confirmed against each generated behavioral .v (2026-07-16).
(* blackbox *)
module sky130_sram_32b256w (
    input         clk0,
    input         csb0,
    input         web0,
    input  [3:0]  wmask0,
    input         spare_wen0,
    input  [8:0]  addr0,
    input  [32:0] din0,
    output [32:0] dout0
);
endmodule

(* blackbox *)
module sky130_sram_32b512w (
    input         clk0,
    input         csb0,
    input         web0,
    input  [3:0]  wmask0,
    input         spare_wen0,
    input  [9:0]  addr0,
    input  [32:0] din0,
    output [32:0] dout0
);
endmodule

(* blackbox *)
module sky130_sram_8b1024w (
    input         clk0,
    input         csb0,
    input         web0,
    input         spare_wen0,
    input  [10:0] addr0,
    input  [8:0]  din0,
    output [8:0]  dout0
);
endmodule
