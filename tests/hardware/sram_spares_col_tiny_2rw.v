`timescale 1ns/1ps
// A spare-augmented behavioral model for a genuinely dual-read/write-port
// memory, combining sram_spares_tiny_2rw.v's per-port-independent timing
// (both ports fully read/write, one shared mem[] array, registered read +
// same-cycle write per port, defect-forcing applied INDEPENDENTLY in each
// port's own write-commit block) with sram_spares_col_tiny_1r1w.v's
// spare-COLUMN convention (MEM_DATA_WIDTH-wide din/dout PER PORT, a
// spare_wenN pin on EACH port -- both are structurally write-capable here,
// unlike 1r1w's single write-only port). The DUT for march-2rw's on-chip
// column repair: the multi-port wrapper branch's first TWO-INDEPENDENT-
// INSTANCE repair_remap_col wiring (one per port, since neither port is
// exclusively "the" reader or writer the way 1r1w's ports are).
//
// The TWO baked-in defects are the SAME bit (3) in DIFFERENT rows (1 and 2),
// mirroring sram_spares_col_tiny.v's/sram_spares_col_tiny_1r1w.v's own
// decisive scenario exactly -- with NUM_SPARE_ROWS=1, two distinct faulty
// rows cannot be covered by the single spare row alone, forcing the
// recurrence onto the spare column.
//
// ADDR_WIDTH/DATA_WIDTH/NUM_SPARE_ROWS/NUM_SPARE_COLS ARE driven by the wrapper
// instantiation; the defaults here just document the intended shape.
module sram_spares_col_tiny_2rw #(
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
    // Port 0: full read/write.
    input  logic                        clk0,
    input  logic                        csb0,
    input  logic                        web0,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr0,
    input  logic [MEM_DATA_WIDTH-1:0]   din0,
    output logic [MEM_DATA_WIDTH-1:0]   dout0,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen0,

    // Port 1: full read/write.
    input  logic                        clk1,
    input  logic                        csb1,
    input  logic                        web1,
    input  logic [MEM_ADDR_WIDTH-1:0]   addr1,
    input  logic [MEM_DATA_WIDTH-1:0]   din1,
    output logic [MEM_DATA_WIDTH-1:0]   dout1,
    input  logic [((NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1)-1:0] spare_wen1
);

    localparam integer DEPTH = (1 << MEM_ADDR_WIDTH);
    localparam logic [DATA_WIDTH-1:0] DEFECT_MASK  = (1 << DEFECT_BIT);
    localparam logic [DATA_WIDTH-1:0] DEFECT2_MASK = (1 << DEFECT2_BIT);

    // Exactly ONE shared storage array indexed by both ports.
    logic [MEM_DATA_WIDTH-1:0] mem [0:DEPTH-1];

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
            // LOGICAL half -- part-selected so a spare lane can NEVER be written
            // as a side effect of the word store (see the reference model). Both
            // defects are stuck-at-1, so only the write-0 phases can ever
            // surface a mismatch.
            if (DEFECT_ADDR >= 0 && addr0 == DEFECT_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= DEFECT_SA1
                    ? (din0[DATA_WIDTH-1:0] |  DEFECT_MASK)
                    : (din0[DATA_WIDTH-1:0] & ~DEFECT_MASK);
            end else if (DEFECT2_ADDR >= 0 && addr0 == DEFECT2_ADDR) begin
                mem[addr0][DATA_WIDTH-1:0] <= DEFECT2_SA1
                    ? (din0[DATA_WIDTH-1:0] |  DEFECT2_MASK)
                    : (din0[DATA_WIDTH-1:0] & ~DEFECT2_MASK);
            end else begin
                mem[addr0][DATA_WIDTH-1:0] <= din0[DATA_WIDTH-1:0];
            end

            // SPARE lanes: written ONLY under spare_wen0, and never defect-aware.
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen0[k]) mem[addr0][DATA_WIDTH + k] <= din0[DATA_WIDTH + k];
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

            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (spare_wen1[k]) mem[addr1][DATA_WIDTH + k] <= din1[DATA_WIDTH + k];
            end
        end

        if (!csb1_q && web1_q) begin
            dout1 <= mem[addr1_q];
        end
    end

endmodule
