// Port-only blackbox stubs for the OpenRAM sky130 macros (synthesis view
// for LibreLane/yosys -- do NOT include in simulation builds alongside the
// behavioral sky130_sram_*.v models: the module names collide by design).
// Pinouts confirmed against each generated behavioral .v (2026-07-16, and
// 2026-07-31 for the dual-port macro below).
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

// Genuinely dual-port (num_r_ports=1, num_w_ports=1): port 0 is write-only
// (no web0 -- wmask0 all-zero is this port's "not writing" state), port 1
// is read-only. See flow/newalgo/sram_wrap_1r1w.sv for the adapter that
// bridges march-1r1w's single-bit `we` onto this port's wmask bus.
(* blackbox *)
module sky130_sram_1r1w_32b256w (
    input         clk0,
    input         csb0,
    input  [3:0]  wmask0,
    input         spare_wen0,
    input  [8:0]  addr0,
    input  [32:0] din0,
    input         clk1,
    input         csb1,
    input  [8:0]  addr1,
    output [32:0] dout1
);
endmodule
