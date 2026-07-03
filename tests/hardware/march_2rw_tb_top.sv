`timescale 1ns/1ps

// Standalone testbench top for the 2RW march algorithm proof-of-concept.
// Instantiates march_2rw_top plus sram_model_2rw wired together, exposing
// bist_start/rst_n/clk as toplevel ports for cocotb to drive -- mirrors
// tests/hardware/march_1r1w_tb_top.sv exactly, generalized to both ports
// being full read/write.
//
// An optional fault-injection path is added on port 0's read side ONLY for
// this testbench (not in sram_model_2rw.sv itself): a per-bit XOR mask
// applied to dout0 as it leaves the memory model, driven by force/release
// from the cocotb test. This lets the test inject a genuine stuck-at-like
// fault without ever touching bist_fail directly.
module march_2rw_tb_top #(
    parameter integer ADDR_WIDTH   = 6,
    parameter integer DATA_WIDTH   = 8,
    parameter integer READ_LATENCY = 1
) (
    input  logic clk,
    input  logic rst_n,
    input  logic bist_start,

    output logic bist_busy,
    output logic bist_done,
    output logic bist_fail
);

    logic                  sram_clk0;
    logic                  sram_csb0;
    logic                  sram_web0;
    logic [ADDR_WIDTH-1:0] sram_addr0;
    logic [DATA_WIDTH-1:0] sram_din0;
    logic [DATA_WIDTH-1:0] sram_dout0_raw;
    logic [DATA_WIDTH-1:0] sram_dout0_faulted;

    logic                  sram_clk1;
    logic                  sram_csb1;
    logic                  sram_web1;
    logic [ADDR_WIDTH-1:0] sram_addr1;
    logic [DATA_WIDTH-1:0] sram_din1;
    logic [DATA_WIDTH-1:0] sram_dout1;

    // Fault-injection mask: cocotb drives this via hierarchical access.
    // XOR-ing a '1' bit into port 0's read path flips that bit as observed
    // by the FSM, modeling a stuck-at fault detection path WITHOUT touching
    // bist_fail directly (the FSM must still notice the mismatch itself).
    logic [DATA_WIDTH-1:0] fault_xor_mask;

    initial begin
        fault_xor_mask = '0;
    end

    assign sram_dout0_faulted = sram_dout0_raw ^ fault_xor_mask;

    march_2rw_top #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(READ_LATENCY)
    ) u_algo_top (
        .clk(clk),
        .rst_n(rst_n),
        .bist_start(bist_start),
        .bist_busy(bist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .sram_clk0(sram_clk0),
        .sram_csb0(sram_csb0),
        .sram_web0(sram_web0),
        .sram_addr0(sram_addr0),
        .sram_din0(sram_din0),
        .sram_dout0(sram_dout0_faulted),
        .sram_clk1(sram_clk1),
        .sram_csb1(sram_csb1),
        .sram_web1(sram_web1),
        .sram_addr1(sram_addr1),
        .sram_din1(sram_din1),
        .sram_dout1(sram_dout1)
    );

    sram_model_2rw #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_sram (
        .clk0(sram_clk0),
        .csb0(sram_csb0),
        .web0(sram_web0),
        .addr0(sram_addr0),
        .din0(sram_din0),
        .dout0(sram_dout0_raw),
        .clk1(sram_clk1),
        .csb1(sram_csb1),
        .web1(sram_web1),
        .addr1(sram_addr1),
        .din1(sram_din1),
        .dout1(sram_dout1)
    );

endmodule
