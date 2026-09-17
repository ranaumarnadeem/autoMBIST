`timescale 1ns/1ps
// A spare-augmented behavioral model for a 1-read-port + 1-write-port memory,
// combining sram_spares_tiny_1r1w.v's dual-port shape (one shared mem[]
// array, port 0 read-only/registered, port 1 write-only/same-cycle) with
// sram_spares_col_tiny.v's spare-COLUMN convention (MEM_DATA_WIDTH-wide
// dout0/din1, a spare_wen1 pin on the write-only port -- the only port type
// that can structurally carry it, see generator.py's
// OPTIONAL_PORT_KEYS_BY_TYPE). The DUT for march-1r1w's on-chip column
// repair: the multi-port wrapper branch's FIRST-ever repair_remap_col
// instance, cross-wired across two physically distinct ports (write port's
// din/spare_wen, read port's dout) rather than one shared rw port.
//
// The TWO baked-in defects are the SAME bit (3) in DIFFERENT rows (1 and 2),
// mirroring sram_spares_col_tiny.v's own decisive scenario exactly -- with
// NUM_SPARE_ROWS=1, two distinct faulty rows cannot be covered by the single
// spare row alone, forcing the recurrence onto the spare column. This proves
// the cross-port repair_remap_col wiring is correct, not just declared: the
// write port's defect and the read port's steered readback must agree.
//
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS ARE driven by the wrapper
// instantiation; the defaults here just document the intended shape.
module sram_spares_col_tiny_1r1w #(
    parameter integer ADDR_WIDTH     = 2,
    parameter integer DATA_WIDTH     = 4,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer MEM_ADDR_WIDTH = $clog2((1 << ADDR_WIDTH) + NUM_SPARE_ROWS),
    parameter integer MEM_DATA_WIDTH = DATA_WIDTH + NUM_SPARE_COLS,
    parameter integer DEFECT_ADDR    = 1,    // physical row 1, ...
    parameter integer DEFECT_BIT     = 3,    // ... top logical bit
    parameter integer DEFECT_SA1     = 1,    // stuck-at-1
    parameter integer DEFECT2_ADDR   = 2,    // physical row 2, ...
    parameter integer DEFECT2_BIT    = 3,    // ... THE SAME BIT -- forces a column repair
    parameter integer DEFECT2_SA1    = 1
) (
    // Port 0: read-only.
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    output logic [MEM_DATA_WIDTH-1:0]   dout0,

    // Port 1: write-only.
    input  logic                        clk1,
    input  logic                        csb1,
    input  logic                        web1,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr1,
    input  logic [MEM_DATA_WIDTH-1:0]   din1,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen1
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    // Exactly ONE shared storage array indexed by both ports.
    logic [MEM_DATA_WIDTH-1:0] mem [0:DEPTH-1];

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
            // LOGICAL half -- part-selected so a spare lane can NEVER be written
            // as a side effect of the word store (see the reference model).
            if (DEFECT_ADDR >= 0 && addr1 == DEFECT_ADDR) begin
                mem[addr1][DATA_WIDTH-1:0] <= DEFECT_SA1
                    ? (din1[DATA_WIDTH-1:0] |  DEFECT_MASK)
                    : (din1[DATA_WIDTH-1:0] & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr1 == DEFECT2_ADDR) begin
                mem[addr1][DATA_WIDTH-1:0] <= DEFECT2_SA1
                    ? (din1[DATA_WIDTH-1:0] |  DEFECT2_MASK)
                    : (din1[DATA_WIDTH-1:0] & ~DEFECT2_MASK);
            end else begin
                mem[addr1][DATA_WIDTH-1:0] <= din1[DATA_WIDTH-1:0];
            end

            // SPARE lanes: written ONLY under spare_wen1, and never defect-aware.
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen1[k]) mem[addr1][DATA_WIDTH + k] <= din1[DATA_WIDTH + k];
            end
        end
    end

endmodule
