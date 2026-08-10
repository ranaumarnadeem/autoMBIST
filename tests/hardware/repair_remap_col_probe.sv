`timescale 1ns/1ps
// Standalone EXHAUSTIVE self-checking probe for rtl/repair_remap_col.sv.
//
// Drives the column-remap module directly -- no wrapper, no memory, no MBIST --
// and sweeps every reachable input combination against an independently-written
// golden model. Same "prove the module before proving the integration" role
// algo_module_probe.sv plays for the algo FSMs, so that a later wrapper-template
// failure can never be misdiagnosed as a bit-steer bug.
//
// Two instances, both swept exhaustively:
//   DUT1  DATA_WIDTH=4, NUM_SPARE_COLS=1  ->  4 * 2 * 16 * 32     =  4,096 vectors
//   DUT2  DATA_WIDTH=4, NUM_SPARE_COLS=2  -> 16 * 4 * 16 * 64     = 65,536 vectors
// DUT2 is what proves the PACKED multi-spare faulty_bit slicing and the
// last-match-wins precedence when two spares name the same logical bit.
//
// Run standalone (no cocotb):  iverilog -g2012 -o probe <this> rtl/repair_remap_col.sv
//                              vvp probe
// Prints "PROBE PASS <n> vectors" on success; $fatal on the first mismatch.
module repair_remap_col_probe;

    localparam integer DW = 4;

    integer errors = 0;
    integer vectors = 0;

    // ---------------- DUT1: one spare column ----------------
    localparam integer N1  = 1;
    localparam integer BI1 = 2;   // $clog2(4)

    logic [DW-1:0]        d1_din_in;
    logic [DW+N1-1:0]     d1_din_out;
    logic [DW+N1-1:0]     d1_dout_in;
    logic [DW-1:0]        d1_dout_out;
    logic [N1-1:0]        d1_spare_wen;
    logic [N1-1:0]        d1_col_repair_en;
    logic [N1*BI1-1:0]    d1_faulty_bit;

    repair_remap_col #(.DATA_WIDTH(DW), .NUM_SPARE_COLS(N1)) dut1 (
        .din_in(d1_din_in), .din_out(d1_din_out),
        .dout_in(d1_dout_in), .dout_out(d1_dout_out),
        .spare_wen(d1_spare_wen),
        .col_repair_en(d1_col_repair_en), .faulty_bit(d1_faulty_bit)
    );

    // ---------------- DUT2: two spare columns ----------------
    localparam integer N2  = 2;
    localparam integer BI2 = 2;

    logic [DW-1:0]        d2_din_in;
    logic [DW+N2-1:0]     d2_din_out;
    logic [DW+N2-1:0]     d2_dout_in;
    logic [DW-1:0]        d2_dout_out;
    logic [N2-1:0]        d2_spare_wen;
    logic [N2-1:0]        d2_col_repair_en;
    logic [N2*BI2-1:0]    d2_faulty_bit;

    repair_remap_col #(.DATA_WIDTH(DW), .NUM_SPARE_COLS(N2)) dut2 (
        .din_in(d2_din_in), .din_out(d2_din_out),
        .dout_in(d2_dout_in), .dout_out(d2_dout_out),
        .spare_wen(d2_spare_wen),
        .col_repair_en(d2_col_repair_en), .faulty_bit(d2_faulty_bit)
    );

    // Golden model, written independently of the DUT's loop structure.
    // exp_din_out: logical bits pass through; spare k copies the bit it replaces.
    // exp_dout_out: per logical bit, the LAST enabled spare naming it wins
    //               (matching the DUT's ascending-k loop precedence).
    task automatic check(
        input integer            nspares,
        input integer            bidx_w,
        input logic [DW-1:0]     din_in,
        input logic [DW+1:0]     dout_in,     // wide enough for either DUT
        input integer            col_en,
        input integer            fbits,
        input logic [DW+1:0]     got_din_out,
        input logic [DW-1:0]     got_dout_out,
        input integer            got_spare_wen
    );
        logic [DW+1:0] exp_din_out;
        logic [DW-1:0] exp_dout_out;
        integer        k, b, sel;
        begin
            exp_din_out = '0;
            exp_din_out[DW-1:0] = din_in;
            for (k = 0; k < nspares; k++) begin
                sel = (fbits >> (k*bidx_w)) & ((1 << bidx_w) - 1);
                exp_din_out[DW + k] = din_in[sel];
            end

            for (b = 0; b < DW; b++) begin
                exp_dout_out[b] = dout_in[b];
                for (k = 0; k < nspares; k++) begin
                    sel = (fbits >> (k*bidx_w)) & ((1 << bidx_w) - 1);
                    if (((col_en >> k) & 1) && sel == b)
                        exp_dout_out[b] = dout_in[DW + k];
                end
            end

            vectors = vectors + 1;

            // Compare the full DW+2 vectors, not a variable part-select (Icarus
            // rejects a non-constant part-select). Safe because both sides pad
            // their unused top bits with 0: the golden model starts from '0 and
            // only assigns DW+k for k < nspares, and the caller zero-extends a
            // narrower DUT output to the same width.
            if (got_din_out !== exp_din_out) begin
                $display("MISMATCH din_out: n=%0d din=%b fb=%0d en=%0d got=%b exp=%b",
                         nspares, din_in, fbits, col_en, got_din_out, exp_din_out);
                errors = errors + 1;
            end
            if (got_dout_out !== exp_dout_out) begin
                $display("MISMATCH dout_out: n=%0d dout_in=%b fb=%0d en=%0d got=%b exp=%b",
                         nspares, dout_in, fbits, col_en, got_dout_out, exp_dout_out);
                errors = errors + 1;
            end
            // spare_wen must be exactly col_repair_en -- the "inert when repair
            // is off" invariant the module header documents.
            if (got_spare_wen !== col_en) begin
                $display("MISMATCH spare_wen: n=%0d got=%0d exp=%0d",
                         nspares, got_spare_wen, col_en);
                errors = errors + 1;
            end
        end
    endtask

    integer fb, en, di, dO;

    initial begin
        // ---- DUT1: 4 * 2 * 16 * 32 = 4096 vectors ----
        for (fb = 0; fb < (1 << (N1*BI1)); fb++)
        for (en = 0; en < (1 << N1); en++)
        for (di = 0; di < (1 << DW); di++)
        for (dO = 0; dO < (1 << (DW+N1)); dO++) begin
            d1_faulty_bit    = fb[N1*BI1-1:0];
            d1_col_repair_en = en[N1-1:0];
            d1_din_in        = di[DW-1:0];
            d1_dout_in       = dO[DW+N1-1:0];
            #1;
            check(N1, BI1, d1_din_in, {2'b0, d1_dout_in}, en, fb,
                  {1'b0, d1_din_out}, d1_dout_out, d1_spare_wen);
        end

        // ---- DUT2: 16 * 4 * 16 * 64 = 65536 vectors ----
        for (fb = 0; fb < (1 << (N2*BI2)); fb++)
        for (en = 0; en < (1 << N2); en++)
        for (di = 0; di < (1 << DW); di++)
        for (dO = 0; dO < (1 << (DW+N2)); dO++) begin
            d2_faulty_bit    = fb[N2*BI2-1:0];
            d2_col_repair_en = en[N2-1:0];
            d2_din_in        = di[DW-1:0];
            d2_dout_in       = dO[DW+N2-1:0];
            #1;
            check(N2, BI2, d2_din_in, d2_dout_in, en, fb,
                  d2_din_out, d2_dout_out, d2_spare_wen);
        end

        if (errors != 0) begin
            $display("PROBE FAIL %0d errors over %0d vectors", errors, vectors);
            $fatal(1);
        end
        $display("PROBE PASS %0d vectors", vectors);
        $finish;
    end

endmodule
