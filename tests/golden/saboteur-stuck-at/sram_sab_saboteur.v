`timescale 1ns/1ps

module sram_sab_saboteur #(
    parameter integer ADDR_WIDTH = 4,
    parameter integer DATA_WIDTH = 8
) (
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic                  web0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    input  logic [DATA_WIDTH-1:0] din0,
    output logic [DATA_WIDTH-1:0] dout0,
    output logic [DATA_WIDTH-1:0] dbg_actual_word,
    output logic [DATA_WIDTH-1:0] dbg_fault_word,
    output logic [DATA_WIDTH-1:0] dbg_intended_word,
    output logic [DATA_WIDTH-1:0] dbg_prior_value,
    output logic [DATA_WIDTH-1:0] dbg_attempted_write,
    output logic [DATA_WIDTH-1:0] dbg_actual_read,
    output logic [DATA_WIDTH-1:0] dbg_blocked_bits
);

    logic [DATA_WIDTH-1:0] real_dout;
    logic [DATA_WIDTH-1:0] real_din;
    logic write_cycle;
    logic read_cycle;

    assign write_cycle = (~csb0) & (~web0);
    assign read_cycle  = (~csb0) & (web0);

    logic [DATA_WIDTH-1:0] sa0_mask [0:(1<<ADDR_WIDTH)-1];
    logic [DATA_WIDTH-1:0] sa1_mask [0:(1<<ADDR_WIDTH)-1];

    initial begin
        $readmemh("<OUT>/sram_sab/faults/sa0_faults.hex", sa0_mask);
        $readmemh("<OUT>/sram_sab/faults/sa1_faults.hex", sa1_mask);
    end

    sram_sab #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_sram (
        .clk0(clk0),
        .csb0(csb0),
        .web0(web0),
        .addr0(addr0),
        .din0(real_din),
        .dout0(real_dout)
    );

    assign real_din = din0;
    assign dout0 = (real_dout & sa0_mask[addr0]) | sa1_mask[addr0];
    assign dbg_actual_word = real_dout;
    assign dbg_fault_word = dout0;
    assign dbg_intended_word = din0;
    assign dbg_prior_value = '0;
    assign dbg_attempted_write = '0;
    assign dbg_actual_read = real_dout;
    assign dbg_blocked_bits = '0;

endmodule
