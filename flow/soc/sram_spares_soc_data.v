`timescale 1ns/1ps
// A DATA_WIDTH=32 sibling of tests/hardware/sram_spares_intmem_a.v, sized
// for this SoC demo's data memory: 5-bit address (32 words) x 32-bit data,
// 1 spare row.
//
// Baked-in defect at word 10 (stuck-at-1, bit 4) -- deliberately placed
// INSIDE the address range the CPU's test program actually writes and
// reads back through real lw/sw instructions. This is the memory whose
// repair the SoC-level proof depends on: if self-repair didn't genuinely
// fix this cell, the CPU's own read-back-and-compare loop would land on
// the FAIL path, not a bypass scan catching it after the fact.
module sram_spares_soc_data #(
    parameter integer ADDR_WIDTH     = 5,
    parameter integer DATA_WIDTH     = 32,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = 10,
    parameter integer DEFECT_BIT     = 4,
    parameter integer DEFECT_SA1     = 1,
    parameter integer DEFECT2_ADDR   = -1,
    parameter integer DEFECT2_BIT    = 0,
    parameter integer DEFECT2_SA1    = 1
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [DATA_WIDTH-1:0]       din0,
    output logic [DATA_WIDTH-1:0]       dout0
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    logic [DATA_WIDTH-1:0]     mem [0:DEPTH-1];

    logic                      csb0_q;
    logic                      web0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;

        if (!csb0 && !web0) begin
            if (DEFECT_ADDR >= 0 && addr0 == DEFECT_ADDR) begin
                mem[addr0] <= DEFECT_SA1 ? (din0 | DEFECT_MASK) : (din0 & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr0 == DEFECT2_ADDR) begin
                mem[addr0] <= DEFECT2_SA1 ? (din0 | DEFECT2_MASK) : (din0 & ~DEFECT2_MASK);
            end else begin
                mem[addr0] <= din0;
            end
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
