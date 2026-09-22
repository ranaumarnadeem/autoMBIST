// word_oriented_engine.sv
// Intra-word coupling-fault (CFid/CFdst) march engine, per van de Goor &
// Tlili, "March tests for word-oriented memories," DATE 1998, Section 5.
//
//   +DBS_FILE=<file>  one hex DW-bit literal per line (the data-background
//                     sequence): the paper's own Level-0..Level(ceil(log2
//                     DW)-1) recursive construction (Section 4.2), computed
//                     in Python (word_oriented.py's dbs_sequence()) -- see
//                     engine/README.md's "Word-oriented intra-word coverage"
//                     section for the citation and the sequence's own
//                     derivation. d = 3 + 3*ceil(log2(DW)) lines.
//   +CFDST_MODE       if present, reads each DBS value TWICE per address
//                     (w_Di, r_Di, r_Di) instead of once (w_Di, r_Di) -- a
//                     conservative superset of the paper's own further-
//                     optimized minimal CFdst sequence, not the minimal
//                     sequence itself (implementation-simplicity tradeoff,
//                     see engine/README.md). Absent: CFid mode.
//   plus all fault_ram plusargs (+FAULTS, +FAULT_INDEX, +INIT, +FAULT_VERBOSE)
//
// AW/DW are top parameters, exactly like march_engine.sv, so compile_engine()
// (algo_engine.py) needs zero changes to build this -- it already passes
// -GAW=<n> -GDW=<n> generically.
//
// No AlgSpec/.alg file involved: this is not a march test in the .alg
// grammar's sense at all (alg_spec.py's MAX_OPS=8 can't even hold a single
// DW=4 DBS sequence -- 2*9=18 ops -- regardless of any op-vocabulary
// question), so it is driven directly, mirroring the ALREADY-shipped
// march_engine_mp.sv precedent of "a structurally different top-level
// testbench, same unmodified fault_ram.sv underneath."
//
// One march element, applied at every address (direction doesn't matter --
// van de Goor's own notation uses up-or-down "up" for this element): for
// each address, write D0 then read D0, write D1 then read D1, ..., through
// the whole DBS. Prints exactly one line beginning with RESULT, the same
// grammar every other engine here uses (algo_engine.py's
// RESULT_DETECTED_RE/RESULT_ESCAPED_RE parse this identically):
//   RESULT DETECTED alg=<a> elem=0 op=<dbs-index> addr=<n> xor=<bits>
//   RESULT ESCAPED  alg=<a>
// alg is "CFID_WOM" (default) or "CFDST_WOM" (+CFDST_MODE), matching this
// project's own convention of naming the algorithm in the RESULT line even
// though it isn't ALG/ALG_FILE-driven here.

`timescale 1ns/1ps

module word_oriented_engine #(
  parameter int AW = 8,
  parameter int DW = 8
);

  localparam int DEPTH = 1 << AW;

  logic clk = 0;
  logic csb = 1, web = 1;
  logic [DW-1:0] wmask = '1;
  logic [AW-1:0] addr = '0;
  logic [DW-1:0] din = '0, dout;

  fault_ram #(.ADDR_WIDTH(AW), .DATA_WIDTH(DW)) dut (
    .clk(clk), .csb(csb), .web(web), .wmask(wmask),
    .addr(addr), .din(din), .dout(dout)
  );

  always #5 clk = ~clk;

  logic [DW-1:0] dbs_q[$];
  bit cfdst_mode;
  string dbs_file;
  string alg_name;

  function automatic void load_dbs(string fpath);
    int fd;
    string line;
    logic [DW-1:0] v;
    fd = $fopen(fpath, "r");
    if (fd == 0) begin
      $display("FATAL: cannot open DBS_FILE %s", fpath);
      $finish;
    end
    while ($fgets(line, fd) != 0) begin
      if (line.len() == 0 || line.substr(0,0) == "#") continue;
      if ($sscanf(line, "%h", v) == 1) dbs_q.push_back(v);
    end
    $fclose(fd);
    if (dbs_q.size() == 0) begin
      $display("FATAL: no DBS values parsed from DBS_FILE %s", fpath);
      $finish;
    end
  endfunction

  task automatic do_write(input int a, input logic [DW-1:0] val);
    @(negedge clk);
    csb = 0; web = 0; addr = a[AW-1:0]; din = val;
    @(posedge clk);
    @(negedge clk);
    csb = 1; web = 1;
  endtask

  int det_elem, det_op, det_addr;
  logic [DW-1:0] det_xor;
  bit detected = 0;

  task automatic do_read(input int a, input logic [DW-1:0] val, input int ei, input int oi);
    @(negedge clk);
    csb = 0; web = 1; addr = a[AW-1:0];
    @(posedge clk);        // dout updates here
    @(negedge clk);
    csb = 1;
    if (dout !== val && !detected) begin
      detected = 1;
      det_elem = ei; det_op = oi; det_addr = a;
      det_xor  = dout ^ val;
    end
  endtask

  initial begin
    if (!$value$plusargs("DBS_FILE=%s", dbs_file)) begin
      $display("FATAL: word_oriented_engine needs +DBS_FILE=<path>");
      $finish;
    end
    load_dbs(dbs_file);
    cfdst_mode = $test$plusargs("CFDST_MODE");
    alg_name = cfdst_mode ? "CFDST_WOM" : "CFID_WOM";

    repeat (4) @(negedge clk);

    for (int a = 0; a < DEPTH && !detected; a++) begin
      for (int i = 0; i < dbs_q.size() && !detected; i++) begin
        do_write(a, dbs_q[i]);
        do_read(a, dbs_q[i], 0, i);
        if (cfdst_mode && !detected) do_read(a, dbs_q[i], 1, i);
      end
    end

    if (detected)
      $display("RESULT DETECTED alg=%s elem=%0d op=%0d addr=%0d xor=%b",
               alg_name, det_elem, det_op, det_addr, det_xor);
    else
      $display("RESULT ESCAPED alg=%s", alg_name);
    $finish;
  end

endmodule
