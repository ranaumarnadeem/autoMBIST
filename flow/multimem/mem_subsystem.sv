`timescale 1ns/1ps
// -----------------------------------------------------------------------------
// mem_subsystem: a small "user design" that instantiates THREE differently-sized
// OpenRAM sky130 SRAM macros behind one synchronous bus, selected by `mem_sel`.
//
//   slot 0 : sky130_sram_32b256w   32b x 256   (1 KB)
//   slot 1 : sky130_sram_32b512w   32b x 512   (2 KB)
//   slot 2 : sky130_sram_8b1024w    8b x 1024  (1 KB, deep/narrow)
//
// Each macro is spare-augmented (1 spare row + 1 spare col -- sky130's paired
// array tiling requires BOTH spare dims odd, so the spare column comes along
// for free). Row repair uses the spare row, addressable at the top of each
// macro's address space; the spare COLUMN is unused here, so each slot ties
// spare_wen0=0, zero-pads the top din bit, and drops the top dout bit --
// presenting a clean D-bit word to the bus. The 32-bit macros carry a per-byte
// write mask (write_size=8); full-word writes tie it all-ones. Reads are
// registered (OpenRAM 1-cycle latency), so the read mux selects with a
// 1-cycle-delayed mem_sel.
//
// This is the design-under-test that autoMBIST's MBIST + on-chip self-repair
// wrappers target (one wrapper per memory, or a shared controller).
//
// Sim: flow/multimem/sky130_sram_*.v (behavioral). Synthesis: sky130_srams_bb.v
// (blackboxes) + the macros' GDS/LEF as LibreLane MACROS entries.
// -----------------------------------------------------------------------------
module mem_subsystem #(
    parameter integer DATA_W = 32,   // system word (widest macro)
    parameter integer ADDR_W = 11    // system address (deepest macro, incl. its spare row)
)(
    input  logic              clk,
    input  logic              csb,      // active-low chip select
    input  logic              we,       // 1 = write, 0 = read
    input  logic [1:0]        mem_sel,  // 0/1/2 -> which memory
    input  logic [ADDR_W-1:0] addr,
    input  logic [DATA_W-1:0] wdata,
    output logic [DATA_W-1:0] rdata
);
    // Per-macro geometry -- CONFIRMED against the generated *.v (2026-07-16):
    // macro DATA_WIDTH is D+1 (the extra bit is the unused spare column); the
    // 32-bit macros carry wmask0[3:0] (write_size=8), the 8-bit one has no
    // wmask (word_size == write_size). A is the macro ADDR_WIDTH and includes
    // the spare-row region above the logical top address.
    localparam integer A0 = 9,  D0 = 32;   // sky130_sram_32b256w  (ADDR_WIDTH=9,  DATA_WIDTH=33)
    localparam integer A1 = 10, D1 = 32;   // sky130_sram_32b512w  (ADDR_WIDTH=10, DATA_WIDTH=33)
    localparam integer A2 = 11, D2 = 8;    // sky130_sram_8b1024w  (ADDR_WIDTH=11, DATA_WIDTH=9)

    // Per-memory active-low chip select: only the addressed slot is enabled.
    wire csb0 = csb | (mem_sel != 2'd0);
    wire csb1 = csb | (mem_sel != 2'd1);
    wire csb2 = csb | (mem_sel != 2'd2);

    wire [D0:0] m0_dout;   // macro dout is D+1 (data + unused spare col)
    wire [D1:0] m1_dout;
    wire [D2:0] m2_dout;

    // ---- slot 0 : 32b x 256 ----
    sky130_sram_32b256w u_m0 (
        .clk0(clk), .csb0(csb0), .web0(~we),
        .wmask0(4'hF),                    // full-word writes (per-byte mask)
        .addr0(addr[A0-1:0]),
        .din0({1'b0, wdata[D0-1:0]}),
        .dout0(m0_dout),
        .spare_wen0(1'b0)
    );

    // ---- slot 1 : 32b x 512 ----
    sky130_sram_32b512w u_m1 (
        .clk0(clk), .csb0(csb1), .web0(~we),
        .wmask0(4'hF),                    // full-word writes (per-byte mask)
        .addr0(addr[A1-1:0]),
        .din0({1'b0, wdata[D1-1:0]}),
        .dout0(m1_dout),
        .spare_wen0(1'b0)
    );

    // ---- slot 2 : 8b x 1024 (no wmask: word_size == write_size) ----
    sky130_sram_8b1024w u_m2 (
        .clk0(clk), .csb0(csb2), .web0(~we),
        .addr0(addr[A2-1:0]),
        .din0({1'b0, wdata[D2-1:0]}),
        .dout0(m2_dout),
        .spare_wen0(1'b0)
    );

    // Registered select to match the memories' 1-cycle read latency.
    logic [1:0] sel_q;
    always_ff @(posedge clk) sel_q <= mem_sel;

    // Narrower slots zero-extend implicitly on assignment to the wider bus.
    always_comb begin
        case (sel_q)
            2'd0:    rdata = m0_dout[D0-1:0];
            2'd1:    rdata = m1_dout[D1-1:0];
            2'd2:    rdata = m2_dout[D2-1:0];
            default: rdata = '0;
        endcase
    end
endmodule
