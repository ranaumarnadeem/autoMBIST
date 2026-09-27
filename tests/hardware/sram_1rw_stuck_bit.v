`timescale 1ns/1ps

// sram_1rw.v with one baked-in defect: bit 0 of address 5 reads back stuck
// at 1 (memory_name: sram_1rw_stuck_bit). For proving a design actually
// DETECTS a fault -- a clean pass alone can be vacuous (a synthesized BIST
// whose compare path was optimized away still passes a good memory).
module sram_1rw_stuck_bit #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer DATA_WIDTH = 32
) (
    input  wire                  clk0,
    input  wire                  csb0,
    input  wire [ADDR_WIDTH-1:0] addr0,
    input  wire [DATA_WIDTH-1:0] din0,
    input  wire                  we0,
    output reg [DATA_WIDTH-1:0]  dout0
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);
    localparam integer DEFECT_ADDR = 5;

    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    reg                  csb0_q;
    reg                  we0_q;
    reg [ADDR_WIDTH-1:0] addr0_q;

    always @(posedge clk0) begin
        csb0_q  <= csb0;
        we0_q   <= we0;
        addr0_q <= addr0;

        if (!csb0 && !we0) begin
            mem[addr0] <= din0;
        end

        if (!csb0_q && we0_q) begin
            dout0 <= (addr0_q == DEFECT_ADDR) ? (mem[addr0_q] | 1) : mem[addr0_q];
        end
    end

endmodule
