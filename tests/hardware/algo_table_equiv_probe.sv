`timescale 1ns/1ps
// EXHAUSTIVE equivalence probe: a RENDERED algorithm table (from
// src/autombist/algo_rtl_gen.py) against the HAND-WRITTEN one it must replace.
//
// This is the load-bearing proof that a .alg spec fully determines the classic
// path's algorithm content. It sweeps the ENTIRE input space the FSM can
// present -- all 8 phase values x all 4 op_step values -- and compares all six
// outputs on every vector. Nothing is sampled or spot-checked.
//
// Why behavioural rather than textual: byte-identity with the hand-written
// files is not achievable and is not the goal. They were written by different
// hands at different times -- mats_plus_algo.sv carries a header block and
// per-arm comments that march_c_algo.sv does not, and the files are a mix of
// LF and CRLF. What matters is that the rendered module BEHAVES identically,
// which a text diff could never establish anyway.
//
// Both module names come in as defines so one probe serves every algorithm:
//   iverilog -g2012 -DREF_MODULE=march_c_algo -DGEN_MODULE=march_c_algo_gen \
//            -o probe <this> rtl/march_c/march_c_algo.sv <rendered>.sv
//   vvp probe
`ifndef REF_MODULE
  `define REF_MODULE march_c_algo
`endif
`ifndef GEN_MODULE
  `define GEN_MODULE march_c_algo_gen
`endif

module algo_table_equiv_probe;

    localparam integer DW = 32;

    logic [2:0]    phase;
    logic [1:0]    op_step;

    logic          r_dir_up, r_do_read, r_do_write, r_last;
    logic [DW-1:0] r_expected, r_write;

    logic          g_dir_up, g_do_read, g_do_write, g_last;
    logic [DW-1:0] g_expected, g_write;

    integer errors  = 0;
    integer vectors = 0;

    `REF_MODULE #(.DATA_WIDTH(DW)) u_ref (
        .phase(phase), .op_step(op_step),
        .phase_dir_up(r_dir_up), .do_read(r_do_read), .do_write(r_do_write),
        .expected_data(r_expected), .write_data(r_write), .last_step(r_last)
    );

    `GEN_MODULE #(.DATA_WIDTH(DW)) u_gen (
        .phase(phase), .op_step(op_step),
        .phase_dir_up(g_dir_up), .do_read(g_do_read), .do_write(g_do_write),
        .expected_data(g_expected), .write_data(g_write), .last_step(g_last)
    );

    // A read op leaves write_data at its default and vice versa, so comparing
    // every output on every vector (rather than only the "relevant" ones) also
    // pins the defaults -- a rendered arm that forgot to leave an unused output
    // alone would show up here.
    task automatic compare;
        begin
            vectors = vectors + 1;
            if (r_dir_up    !== g_dir_up)    begin
                $display("MISMATCH phase_dir_up  phase=%0d op_step=%0d ref=%b gen=%b",
                         phase, op_step, r_dir_up, g_dir_up);
                errors = errors + 1;
            end
            if (r_do_read   !== g_do_read)   begin
                $display("MISMATCH do_read       phase=%0d op_step=%0d ref=%b gen=%b",
                         phase, op_step, r_do_read, g_do_read);
                errors = errors + 1;
            end
            if (r_do_write  !== g_do_write)  begin
                $display("MISMATCH do_write      phase=%0d op_step=%0d ref=%b gen=%b",
                         phase, op_step, r_do_write, g_do_write);
                errors = errors + 1;
            end
            if (r_expected  !== g_expected)  begin
                $display("MISMATCH expected_data phase=%0d op_step=%0d ref=%h gen=%h",
                         phase, op_step, r_expected, g_expected);
                errors = errors + 1;
            end
            if (r_write     !== g_write)     begin
                $display("MISMATCH write_data    phase=%0d op_step=%0d ref=%h gen=%h",
                         phase, op_step, r_write, g_write);
                errors = errors + 1;
            end
            if (r_last      !== g_last)      begin
                $display("MISMATCH last_step     phase=%0d op_step=%0d ref=%b gen=%b",
                         phase, op_step, r_last, g_last);
                errors = errors + 1;
            end
        end
    endtask

    integer p, o;

    initial begin
        for (p = 0; p < 8; p = p + 1) begin
            for (o = 0; o < 4; o = o + 1) begin
                phase   = p[2:0];
                op_step = o[1:0];
                #1;
                compare();
            end
        end

        if (errors != 0) begin
            $display("EQUIV FAIL %0d mismatches over %0d vectors", errors, vectors);
            $fatal(1);
        end
        $display("EQUIV PASS %0d vectors", vectors);
        $finish;
    end

endmodule
