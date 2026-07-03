`timescale 1ns/1ps

// Standalone differential-proof testbench top for the port-coupling fault
// model. Instantiates the REAL generated saboteur module directly (built by
// run_port_coupling_diff_tb.py via generate_from_config(fault_type=
// "port-coupling", algo="march-1r1w") -- the exact same saboteur a real
// autombist run would produce), with its read-port and write-port pins
// exposed raw at this top level so a cocotb test can drive each port by
// hand.
//
// This exists to prove the central claim of the port-coupling fault type:
// the defect is only observable when the read (port 0) and write (port 1)
// genuinely coincide on the SAME clock edge at the SAME address. A
// "test-each-port-separately" (non-concurrent) access pattern touching the
// exact same address with the exact same data, but on non-overlapping
// cycles, must NOT observe the corruption -- only a genuinely concurrent
// access does. See test_port_coupling_diff.py for the two drivers.
module port_coupling_diff_tb_top #(
    parameter integer ADDR_WIDTH = 6,
    parameter integer DATA_WIDTH = 8
) (
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    output logic [DATA_WIDTH-1:0] dout0,

    input  logic                  clk1,
    input  logic                  csb1,
    input  logic                  web1,
    input  logic [ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0] din1,

    output logic [DATA_WIDTH-1:0] dbg_actual_word,
    output logic [DATA_WIDTH-1:0] dbg_fault_word,
    output logic [DATA_WIDTH-1:0] dbg_blocked_bits
);

    sram_1r1w_dut_saboteur #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_saboteur (
        .clk0(clk0),
        .csb0(csb0),
        .addr0(addr0),
        .dout0(dout0),
        .clk1(clk1),
        .csb1(csb1),
        .web1(web1),
        .addr1(addr1),
        .din1(din1),
        .dbg_actual_word(dbg_actual_word),
        .dbg_fault_word(dbg_fault_word),
        .dbg_intended_word(),
        .dbg_prior_value(),
        .dbg_attempted_write(),
        .dbg_actual_read(),
        .dbg_blocked_bits(dbg_blocked_bits)
    );

endmodule
