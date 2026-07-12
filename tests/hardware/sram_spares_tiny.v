`timescale 1ns/1ps
// A renamed copy of rtl/sram_model_spares.sv (module name changed to match the
// test config's memory_name; ports/logic otherwise identical). The DEFECT_*
// parameter DEFAULTS are hardcoded here to the Step-A repair-loop scenario --
// a stuck-at-1 at physical address 3, bit 3 -- because the wrapper generator
// never overrides them (it has no concept of a defect location), exactly as it
// never overrides anything test-specific in sram_tiny.v. ADDR_WIDTH/DATA_WIDTH/
// NUM_SPARE_ROWS ARE driven by the wrapper instantiation; the defaults here just
// document the intended shape (addr_width 2, data_width 4, 2 spare rows).
module sram_spares_tiny #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 2,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = 3,    // physical row 3 is defective
    parameter integer DEFECT_BIT     = 3,    // top bit (endianness guard)
    parameter integer DEFECT_SA1     = 1     // stuck-at-1
) (
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [DATA_WIDTH-1:0]       din0,
    output logic [DATA_WIDTH-1:0]       dout0
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK = (1 << DEFECT_BIT);

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
            end else begin
                mem[addr0] <= din0;
            end
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
