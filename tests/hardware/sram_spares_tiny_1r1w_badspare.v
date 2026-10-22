`timescale 1ns/1ps
// Same dual-port (1-read + 1-write) shape as sram_spares_tiny_1r1w.v, with one
// defective logical row (3, bit 3 stuck at 1) AND a defective SPARE row: physical
// row 4 is spare row 0 at ADDR_WIDTH 2, its bit 1 stuck at 0. The on-chip
// analyzer gives row 3 spare row 0 first, so the verify pass fails; with
// redundancy.onchip_spare_retry the chip retires spare row 0 and moves row 3
// to spare row 1 (tests/integration/test_onchip_spare_retry_e2e.py).
module sram_spares_tiny_1r1w_badspare #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 2,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = 3,    // physical row 3 is defective
    parameter integer DEFECT_BIT     = 3,    // top bit (endianness guard)
    parameter integer DEFECT_SA1     = 1,    // stuck-at-1
    parameter integer DEFECT2_ADDR   = 4,    // physical row 4 = spare row 0 is defective
    parameter integer DEFECT2_BIT    = 1,
    parameter integer DEFECT2_SA1    = 0     // stuck-at-0 (different polarity)
) (
    // Port 0: read-only.
    input  logic                      clk0,
    input  logic                      csb0,
    input  logic [MEM_ADDR_WIDTH-1:0] addr0,
    output logic [DATA_WIDTH-1:0]     dout0,

    // Port 1: write-only.
    input  logic                      clk1,
    input  logic                      csb1,
    input  logic                      web1,
    input  logic [MEM_ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0]     din1
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    // Exactly ONE shared storage array indexed by both ports.
    logic [DATA_WIDTH-1:0]     mem [0:DEPTH-1];

    logic                      csb0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        addr0_q <= addr0;

        if (!csb0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

    always_ff @(posedge clk1) begin
        if (!csb1 && !web1) begin
            if (DEFECT_ADDR >= 0 && addr1 == DEFECT_ADDR) begin
                mem[addr1] <= DEFECT_SA1 ? (din1 | DEFECT_MASK) : (din1 & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr1 == DEFECT2_ADDR) begin
                mem[addr1] <= DEFECT2_SA1 ? (din1 | DEFECT2_MASK) : (din1 & ~DEFECT2_MASK);
            end else begin
                mem[addr1] <= din1;
            end
        end
    end

endmodule
