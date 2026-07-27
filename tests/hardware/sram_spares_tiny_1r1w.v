`timescale 1ns/1ps
// A spare-augmented behavioral model for a 1-read-port + 1-write-port memory,
// combining rtl/sram_model_1r1w.sv's dual-port shape (one shared mem[] array,
// port 0 read-only/registered, port 1 write-only/same-cycle) with
// sram_spares_tiny.v's baked-in-defect convention (module name matches the
// test config's memory_name; DEFECT_* defaults document the Step-A repair
// scenario -- a stuck-at-1 at physical address 3, bit 3 -- since the wrapper
// generator has no concept of a defect location). ADDR_WIDTH/DATA_WIDTH/
// NUM_SPARE_ROWS are driven by the wrapper instantiation.
//
// The defect is injected on port 1's write path only (port 0 is read-only and
// structurally cannot write) -- mirroring sram_spares_tiny.v's single write
// path, just relocated to the write-only port of this dual-port shape.
module sram_spares_tiny_1r1w #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 2,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = 3,    // physical row 3 is defective
    parameter integer DEFECT_BIT     = 3,    // top bit (endianness guard)
    parameter integer DEFECT_SA1     = 1     // stuck-at-1
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
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK = (1 << DEFECT_BIT);

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
            end else begin
                mem[addr1] <= din1;
            end
        end
    end

endmodule
