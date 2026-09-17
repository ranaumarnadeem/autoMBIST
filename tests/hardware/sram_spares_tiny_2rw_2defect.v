`timescale 1ns/1ps
// Same dual-read/write-port shape as sram_spares_tiny_2rw.v, but with BOTH
// defect knobs baked in at two DISTINCT logical rows -- mirroring
// sram_spares_tiny_2defect.v's role for march-c: with NUM_SPARE_ROWS=1,
// BIRA's row-only allocation cannot cover 2 distinct faulty rows, giving the
// unrepairable-case self-repair test (the partial scenario) a genuine "more
// distinct faulty rows than spares" scenario for march-2rw too.
module sram_spares_tiny_2rw_2defect #(
    parameter integer ADDR_WIDTH     = 6,
    parameter integer DATA_WIDTH     = 8,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = 3,    // physical row 3 is defective
    parameter integer DEFECT_BIT     = 3,    // top bit (endianness guard)
    parameter integer DEFECT_SA1     = 1,    // stuck-at-1
    parameter integer DEFECT2_ADDR   = 1,    // physical row 1 is ALSO defective
    parameter integer DEFECT2_BIT    = 1,
    parameter integer DEFECT2_SA1    = 0     // stuck-at-0 (different polarity)
) (
    // Port 0: full read/write.
    input  logic                      clk0,
    input  logic                      csb0,
    input  logic                      web0,
    input  logic [MEM_ADDR_WIDTH-1:0] addr0,
    input  logic [DATA_WIDTH-1:0]     din0,
    output logic [DATA_WIDTH-1:0]     dout0,

    // Port 1: full read/write.
    input  logic                      clk1,
    input  logic                      csb1,
    input  logic                      web1,
    input  logic [MEM_ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0]     din1,
    output logic [DATA_WIDTH-1:0]     dout1
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    // Exactly ONE shared storage array indexed by both ports.
    logic [DATA_WIDTH-1:0]     mem [0:DEPTH-1];

    logic                      csb0_q;
    logic                      web0_q;
    logic [MEM_ADDR_WIDTH-1:0] addr0_q;

    logic                      csb1_q;
    logic                      web1_q;
    logic [MEM_ADDR_WIDTH-1:0] addr1_q;

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

    always_ff @(posedge clk1) begin
        csb1_q  <= csb1;
        web1_q  <= web1;
        addr1_q <= addr1;

        if (!csb1 && !web1) begin
            if (DEFECT_ADDR >= 0 && addr1 == DEFECT_ADDR) begin
                mem[addr1] <= DEFECT_SA1 ? (din1 | DEFECT_MASK) : (din1 & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr1 == DEFECT2_ADDR) begin
                mem[addr1] <= DEFECT2_SA1 ? (din1 | DEFECT2_MASK) : (din1 & ~DEFECT2_MASK);
            end else begin
                mem[addr1] <= din1;
            end
        end

        if (!csb1_q && web1_q) begin
            dout1 <= mem[addr1_q];
        end
    end

endmodule
