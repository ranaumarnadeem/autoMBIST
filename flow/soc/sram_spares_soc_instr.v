`timescale 1ns/1ps
// A DATA_WIDTH=32 sibling of tests/hardware/sram_spares_intmem_a.v (same
// synchronous, spare-row-augmented, defect-injectable behavioral memory),
// sized to hold this SoC demo's instruction memory: 6-bit address (64
// words) x 32-bit data, 1 spare row.
//
// Baked-in defect at word 63 (the LAST addressable word) -- deliberately
// placed past the real program's instruction count so it sits in never-
// fetched padding: the CPU's own halt loop (an infinite self-branch) never
// reaches it. Repair success is checked via self_repair_fail (the
// controller's own per-memory claim) rather than a functional-port scan,
// since nothing ever writes/reads this address to compare against -- that's
// deliberate: it proves repair mechanics succeed for this memory too, with
// zero risk of the CPU ever executing corrupted code if a repair bug
// regressed (the data memory's defect, in the address range the firmware
// actually exercises, is what carries the CPU-level correctness proof).
module sram_spares_soc_instr #(
    parameter integer ADDR_WIDTH     = 6,
    parameter integer DATA_WIDTH     = 32,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer DEFECT_ADDR    = 63,
    parameter integer DEFECT_BIT     = 3,
    parameter integer DEFECT_SA1     = 0,
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

    // Firmware preload is deliberately NOT done here via $readmemh at time 0:
    // self-repair's own march-C BIST runs on this array BEFORE the CPU ever
    // boots (repair-at-boot), and march-C's test patterns write every cell,
    // including address 0 -- any $readmemh-preloaded firmware would simply be
    // overwritten by the BIST pass before the CPU ever got to read it. The
    // testbench instead backdoor-pokes the firmware image directly into `mem`
    // AFTER self_repair_done reads high but BEFORE releasing the CPU from
    // reset (see test_soc_selfrepair.py) -- exactly mirroring how a real
    // power-on-self-test system loads its boot image only after POST/BIST
    // has already finished its destructive pass over the array.
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
