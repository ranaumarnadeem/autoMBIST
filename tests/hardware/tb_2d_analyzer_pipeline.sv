`timescale 1ns/1ps
// rtl/onchip_2d_repair_analyzer.sv against its bit-by-bit reference
// (onchip_2d_repair_analyzer_seq.sv, the module before its claim decision was
// computed from running counts and before its pipeline stage), on one random
// input stream: fails on consecutive cycles over a few rows and columns, with
// verify, latch, retire, defer and repair-load pulses at random, and a reset
// every 300 cycles so the state keeps changing (seen bits are never cleared
// and retired spares stay bad, so one long run would soon decide nothing).
//   * u_flat (PIPELINE 0) must equal the reference every cycle: every output
//     and every piece of state.
//   * u_pipe (PIPELINE 1) must equal the reference a cycle late. Its
//     has_suspect is compared in the same cycle (it counts the fail still in
//     its decision stage), except in the cycle a latch or retire is applied,
//     which it clears a cycle later -- the sequencer never reads it then.
// It also counts the decisions u_pipe made with the forwarded update, and
// fails a run that never needed it.
//
// Run by tests/integration/test_2d_analyzer_pipeline.py; prints
// "PIPELINE RESULT: PASS" or the first mismatches and "PIPELINE RESULT: FAIL".
module tb_2d_analyzer_pipeline;
    parameter integer DEFERRED = 0;
    parameter integer SEED     = 1;
    parameter integer CYCLES   = 20000;
    parameter integer AW       = 5;
    parameter integer DW       = 8;
    parameter integer NR       = 2;
    parameter integer NC       = 2;
    parameter integer WPR      = 4;

    localparam integer BIW   = (DW * WPR > 1) ? $clog2(DW * WPR) : 1;
    localparam integer NROWS = (1 << AW) / WPR;

    logic clk = 1'b0;
    logic rst_n = 1'b0;
    always #5 clk = ~clk;

    logic          enable, fail_valid, latch_result, repair_load, verify_enable, retire, defer;
    logic [AW-1:0] fail_addr;
    logic [DW-1:0] fail_bitmask;
    logic [NR-1:0]    fuse_en;
    logic [NR*AW-1:0] fuse_addr;

    // One analyzer's outputs and state, flattened.
    `define PORTS(U) \
        logic [NR-1:0] U``_row_en; logic [NR*AW-1:0] U``_row_addr; \
        logic [NC-1:0] U``_col_en; logic [NC*BIW-1:0] U``_col_bit; \
        logic U``_load_done, U``_unrep, U``_suspect; \
        logic [NR-1:0] U``_lrv; logic [NR*AW-1:0] U``_lra; \
        logic [NC-1:0] U``_lcv; logic [NC*BIW-1:0] U``_lcb;
    `define CONNECT(U) \
        .clk(clk), .rst_n(rst_n), .enable(enable), .fail_valid(fail_valid), .fail_addr(fail_addr), \
        .fail_bitmask(fail_bitmask), .latch_result(latch_result), .repair_load(repair_load), \
        .fuse_row_repair_en(fuse_en), .fuse_faulty_row_addr(fuse_addr), \
        .repair_load_done(U``_load_done), .row_repair_en(U``_row_en), .faulty_row_addr(U``_row_addr), \
        .col_repair_en(U``_col_en), .faulty_bit(U``_col_bit), .unrepairable(U``_unrep), \
        .verify_enable(verify_enable), .retire(retire), .has_suspect(U``_suspect), .defer(defer)

    `PORTS(r)
    `PORTS(f)
    `PORTS(p)

    onchip_2d_repair_analyzer_seq #(.ADDR_WIDTH(AW), .DATA_WIDTH(DW), .NUM_SPARE_ROWS(NR),
        .NUM_SPARE_COLS(NC), .DEFERRED(DEFERRED), .WORDS_PER_ROW(WPR)) u_ref (`CONNECT(r));
    onchip_2d_repair_analyzer #(.ADDR_WIDTH(AW), .DATA_WIDTH(DW), .NUM_SPARE_ROWS(NR),
        .NUM_SPARE_COLS(NC), .DEFERRED(DEFERRED), .WORDS_PER_ROW(WPR), .PIPELINE(0)) u_flat (`CONNECT(f));
    onchip_2d_repair_analyzer #(.ADDR_WIDTH(AW), .DATA_WIDTH(DW), .NUM_SPARE_ROWS(NR),
        .NUM_SPARE_COLS(NC), .DEFERRED(DEFERRED), .WORDS_PER_ROW(WPR), .PIPELINE(1)) u_pipe (`CONNECT(p));

    genvar gi;
    generate
        for (gi = 0; gi < NR; gi++) begin : g_rows
            assign r_lrv[gi] = u_ref.live_row_valid[gi];  assign r_lra[gi*AW +: AW] = u_ref.live_row_addr[gi];
            assign f_lrv[gi] = u_flat.live_row_valid[gi]; assign f_lra[gi*AW +: AW] = u_flat.live_row_addr[gi];
            assign p_lrv[gi] = u_pipe.live_row_valid[gi]; assign p_lra[gi*AW +: AW] = u_pipe.live_row_addr[gi];
        end
        for (gi = 0; gi < NC; gi++) begin : g_cols
            assign r_lcv[gi] = u_ref.live_col_valid[gi];  assign r_lcb[gi*BIW +: BIW] = u_ref.live_col_bit[gi];
            assign f_lcv[gi] = u_flat.live_col_valid[gi]; assign f_lcb[gi*BIW +: BIW] = u_flat.live_col_bit[gi];
            assign p_lcv[gi] = u_pipe.live_col_valid[gi]; assign p_lcb[gi*BIW +: BIW] = u_pipe.live_col_bit[gi];
        end
    endgenerate

    `define STATE(U, I) {U``_row_en, U``_row_addr, U``_col_en, U``_col_bit, U``_load_done, \
        I.seen_once, I.first_seen, I.first_row, I.bad_row, I.suspect_row, I.bad_col, I.suspect_col, \
        U``_lrv, U``_lra, U``_lcv, U``_lcb}

    logic [4095:0] s_ref, s_flat, s_pipe, ref_prev;
    always_comb begin
        s_ref  = `STATE(r, u_ref);
        s_flat = `STATE(f, u_flat);
        s_pipe = `STATE(p, u_pipe);
    end

    integer cyc, errors, fails, claims, r, s, epochs, forwarded;
    logic   clear_now;

    task automatic report(input string what);
        errors = errors + 1;
        if (errors <= 5) $display("cycle %0d: %s", cyc, what);
    endtask

    initial begin
        s = SEED;
        errors = 0; fails = 0; claims = 0; epochs = 0; forwarded = 0;
        enable = 0; fail_valid = 0; fail_addr = 0; fail_bitmask = 0; latch_result = 0;
        repair_load = 0; verify_enable = 0; retire = 0; defer = 0; fuse_en = 0; fuse_addr = 0;
        repeat (3) @(posedge clk);
        #1 rst_n = 1'b1;
        @(posedge clk);
        #0.5 ref_prev = s_ref;
        for (cyc = 0; cyc < CYCLES; cyc++) begin
            // Inputs change just after a rising edge; the analyzers sample them
            // on the next one.
            #0.5;
            if (cyc % 300 == 299) begin
                {enable, fail_valid, latch_result, retire, repair_load, verify_enable} = '0;
                rst_n = 1'b0;
                @(posedge clk);
                #0.5 rst_n = 1'b1;
                @(posedge clk);
                #0.5 ref_prev = s_ref;
                epochs = epochs + 1;
                continue;
            end
            r             = $urandom(s) % 100;
            enable        = ($urandom(s) % 100) < 90;
            fail_valid    = ($urandom(s) % 100) < 55;   // often on consecutive cycles
            fail_addr     = $urandom(s) % (1 << AW);
            fail_bitmask  = (($urandom(s) % 100) < 70) ? (1 << ($urandom(s) % DW)) : $urandom(s);
            // Biased toward two bits and two physical rows, so recurrences,
            // claims and same-column fails back to back are common.
            if (($urandom(s) % 100) < 60) fail_bitmask = fail_bitmask & 'h0C;
            if (($urandom(s) % 100) < 50)
                fail_addr = (((($urandom(s) % 3) == 0) ? 5 : 2) % NROWS) * WPR + (fail_addr % WPR);
            verify_enable = ($urandom(s) % 100) < 25;
            defer         = ($urandom(s) % 100) < 50;
            latch_result  = r < 4;
            retire        = (r == 4);
            repair_load   = (r == 5);
            fuse_en       = $urandom(s);
            fuse_addr     = {$urandom(s), $urandom(s)};
            clear_now     = latch_result || retire;
            if (fail_valid && enable) fails = fails + 1;
            @(posedge clk);
            #0.5;
            if (s_flat !== s_ref) report($sformatf("unpipelined state %h\n    reference %h", s_flat, s_ref));
            if (f_suspect !== r_suspect) report($sformatf("unpipelined has_suspect %b, reference %b", f_suspect, r_suspect));
            if (s_pipe !== ref_prev) report($sformatf("pipelined state %h\n    reference, a cycle earlier %h", s_pipe, ref_prev));
            if (!clear_now && p_suspect !== r_suspect) report($sformatf("pipelined has_suspect %b, reference %b", p_suspect, r_suspect));
            if (p_unrep !== 1'b0 || f_unrep !== 1'b0 || r_unrep !== 1'b0) report("unrepairable asserted");
            if (|r_lcv) claims = claims + 1;
            if (u_pipe.fwd && (((u_pipe.upd_seen | u_pipe.upd_first) & u_pipe.fb_i) != 0)) forwarded = forwarded + 1;
            ref_prev = s_ref;
        end
        $display("cycles=%0d resets=%0d fails=%0d cycles_with_a_column_claim=%0d forwarded=%0d errors=%0d",
                 CYCLES, epochs, fails, claims, forwarded, errors);
        if (errors == 0 && fails > 1000 && claims > 100 && (WPR == 1 || forwarded > 50))
            $display("PIPELINE RESULT: PASS");
        else
            $display("PIPELINE RESULT: FAIL");
        $finish;
    end
endmodule
