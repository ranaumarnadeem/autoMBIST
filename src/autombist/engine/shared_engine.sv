// shared_engine.sv
// Shared-controller (docs/shared-hierarchical-mbist-plan.md) sibling of
// march_engine.sv -- NOT a modification of that file. One algorithm
// controller sequenced across NUM_MEMORIES separate fault_ram instances,
// one at a time (mux-select, not parallel access) -- mirrors
// wrapper_template.j2's own shared-bus sequencer (step 1 of that plan's
// implementation order): the FULL algorithm program runs to completion
// against memory 0, then restarts from scratch against memory 1, and so
// on, exactly matching the real RTL's own SHB_IDLE/RUN/ADVANCE/DONE
// behavior (a full algorithm pass per memory, never interleaved).
//
// Unlike march_engine_mp.sv (N fixed at 2, hand-duplicated port buses),
// NUM_MEMORIES here is a runtime parameter, so per-memory signals are
// unpacked arrays driven through a generate/genvar loop of fault_ram
// instances -- fault_ram.sv itself is reused completely unchanged, same
// as every prior sibling engine in this project.
//
//   +ALG_FILE=<file>   numeric element/op program (preferred; emitted by autombist)
//   +ALG=MATSP|MARCHCM|MARCHSS   built-in fallback for tool-free smoke tests
//   +BACKGROUND=<hex>  DW-bit data-background mask (default 0 = solid 0/1,
//                      byte-identical to every campaign that omits it)
//   plus all fault_ram plusargs (+FAULTS, +FAULT_INDEX, +INIT, +FAULT_VERBOSE)
//   -- NOTE (step 4 of the plan above): fault targeting is not yet
//   per-memory here (that's step 5's own scope, a real fault_ram.sv/
//   FAULT_TAG change) -- every generate-instantiated fault_ram copy reads
//   the SAME global +FAULTS/+FAULT_INDEX plusarg today, so a non-golden
//   (faulted) run against N>1 memories is not yet meaningful; only a
//   golden (no +FAULTS) run is proven at this step.
//
// AW/DW/WORDS_PER_ROW/NUM_MEMORIES are top parameters (Verilator:
// -GAW=<n> -GDW=<n> -GWORDS_PER_ROW=<n> -GNUM_MEMORIES=<n>), matching
// every other engine's own compile-time-parameter convention.
//
// Prints exactly one line beginning with RESULT, the same grammar every
// other engine here uses (parse_result_line parses this identically,
// ignoring the trailing mem= field until a later step extends it):
//   RESULT DETECTED alg=<a> elem=<e> op=<o> addr=<n> xor=<bits> mem=<mi>
//   RESULT ESCAPED  alg=<a>
//
// Numeric .alg line format and word-background semantics: byte-identical
// to march_engine.sv's own (see that file's header comment) -- load_alg/
// load_alg_from_file/bg_value are reused verbatim, unchanged.

`timescale 1ns/1ps

module shared_engine #(
  parameter int AW = 8,
  parameter int DW = 8,
  parameter int WORDS_PER_ROW = 1,
  parameter int NUM_MEMORIES = 2
);

  localparam int DEPTH = 1 << AW;

  logic clk = 0;
  logic csb [NUM_MEMORIES];
  logic web [NUM_MEMORIES];
  logic [DW-1:0] wmask [NUM_MEMORIES];
  logic [AW-1:0] addr [NUM_MEMORIES];
  logic [DW-1:0] din [NUM_MEMORIES];
  logic [DW-1:0] dout [NUM_MEMORIES];
  logic [DW-1:0] background_mask = '0;

  genvar gi;
  generate
    for (gi = 0; gi < NUM_MEMORIES; gi++) begin : mem_inst
      fault_ram #(.ADDR_WIDTH(AW), .DATA_WIDTH(DW), .WORDS_PER_ROW(WORDS_PER_ROW)) dut (
        .clk(clk), .csb(csb[gi]), .web(web[gi]), .wmask(wmask[gi]),
        .addr(addr[gi]), .din(din[gi]), .dout(dout[gi])
      );
    end
  endgenerate

  always #5 clk = ~clk;

  initial begin
    for (int i = 0; i < NUM_MEMORIES; i++) begin
      csb[i] = 1; web[i] = 1; wmask[i] = '1; addr[i] = '0; din[i] = '0;
    end
  end

  // Nominal value v (0/1) under the current data background: w0/r0 -> mask,
  // w1/r1 -> ~mask. mask=0 (the default) reduces to {DW{v}} exactly, so
  // every campaign that omits +BACKGROUND sees byte-identical behavior.
  function automatic logic [DW-1:0] bg_value(input bit v);
    bg_value = background_mask ^ {DW{v}};
  endfunction

  // op codes: 0=r0 1=r1 2=w0 3=w1 ; -1=wc -2=wcb -3=rc -4=rcb ; dir: 0=up 1=down
  // (identical vocabulary and either-direction-resolution convention as
  // march_engine.sv -- see that file's own comment for the full rationale.)
  typedef struct {
    int dir;
    int nops;
    int ops[8];
  } elem_s;

  elem_s prog[16];
  int    nelem;
  string alg;
  string alg_file;

  function automatic void load_alg(string a);
    case (a)
      "MATSP": begin // {either(w0); up(r0,w1); down(r1,w0)}   5n
        nelem = 3;
        prog[0] = '{dir:0, nops:1, ops:'{2,0,0,0,0,0,0,0}};
        prog[1] = '{dir:0, nops:2, ops:'{0,3,0,0,0,0,0,0}};
        prog[2] = '{dir:1, nops:2, ops:'{1,2,0,0,0,0,0,0}};
      end
      "MARCHCM": begin // March C-   10n
        nelem = 6;
        prog[0] = '{dir:0, nops:1, ops:'{2,0,0,0,0,0,0,0}};
        prog[1] = '{dir:0, nops:2, ops:'{0,3,0,0,0,0,0,0}};
        prog[2] = '{dir:0, nops:2, ops:'{1,2,0,0,0,0,0,0}};
        prog[3] = '{dir:1, nops:2, ops:'{0,3,0,0,0,0,0,0}};
        prog[4] = '{dir:1, nops:2, ops:'{1,2,0,0,0,0,0,0}};
        prog[5] = '{dir:1, nops:1, ops:'{0,0,0,0,0,0,0,0}};
      end
      "MARCHSS": begin // March SS   22n
        nelem = 6;
        prog[0] = '{dir:0, nops:1, ops:'{2,0,0,0,0,0,0,0}};
        prog[1] = '{dir:0, nops:5, ops:'{0,0,2,0,3,0,0,0}};
        prog[2] = '{dir:0, nops:5, ops:'{1,1,3,1,2,0,0,0}};
        prog[3] = '{dir:1, nops:5, ops:'{0,0,2,0,3,0,0,0}};
        prog[4] = '{dir:1, nops:5, ops:'{1,1,3,1,2,0,0,0}};
        prog[5] = '{dir:1, nops:1, ops:'{0,0,0,0,0,0,0,0}};
      end
      default: begin
        $display("FATAL: unknown +ALG=%s", a);
        $finish;
      end
    endcase
  endfunction

  function automatic void load_alg_from_file(string fpath);
    int    fd, n;
    string line;
    int    d, nops, o0, o1, o2, o3, o4, o5, o6, o7;
    nelem = 0;
    fd = $fopen(fpath, "r");
    if (fd == 0) begin
      $display("FATAL: cannot open ALG_FILE %s", fpath);
      $finish;
    end
    while ($fgets(line, fd) != 0 && nelem < 16) begin
      if (line.substr(0,0) == "#") continue;
      o0=0; o1=0; o2=0; o3=0; o4=0; o5=0; o6=0; o7=0;
      n = $sscanf(line, "%d %d %d %d %d %d %d %d %d %d",
                  d, nops, o0, o1, o2, o3, o4, o5, o6, o7);
      if (n < 2) continue;
      prog[nelem].dir  = d;
      prog[nelem].nops = nops;
      prog[nelem].ops[0] = o0;
      prog[nelem].ops[1] = o1;
      prog[nelem].ops[2] = o2;
      prog[nelem].ops[3] = o3;
      prog[nelem].ops[4] = o4;
      prog[nelem].ops[5] = o5;
      prog[nelem].ops[6] = o6;
      prog[nelem].ops[7] = o7;
      nelem++;
    end
    $fclose(fd);
    if (nelem == 0) begin
      $display("FATAL: no elements parsed from ALG_FILE %s", fpath);
      $finish;
    end
  endfunction

  task automatic do_write(input int a, input bit v, input int mi);
    @(negedge clk);
    csb[mi] = 0; web[mi] = 0; addr[mi] = a[AW-1:0]; din[mi] = bg_value(v);
    @(posedge clk);
    @(negedge clk);
    csb[mi] = 1; web[mi] = 1;
  endtask

  task automatic do_wait(input int n, input int mi);
    repeat (n) begin
      @(negedge clk);
      csb[mi] = 1;
      @(posedge clk);
      @(negedge clk);
    end
  endtask

  int det_mem, det_elem, det_op, det_addr;
  logic [DW-1:0] det_xor;
  bit detected = 0;

  task automatic do_read(input int a, input bit v, input int mi,
                         input int ei, input int oi);
    @(negedge clk);
    csb[mi] = 0; web[mi] = 1; addr[mi] = a[AW-1:0];
    @(posedge clk);        // dout updates here
    @(negedge clk);
    csb[mi] = 1;
    if (dout[mi] !== bg_value(v) && !detected) begin
      detected = 1;
      det_mem = mi; det_elem = ei; det_op = oi; det_addr = a;
      det_xor  = dout[mi] ^ bg_value(v);
    end
  endtask

  initial begin
    if ($value$plusargs("ALG_FILE=%s", alg_file)) begin
      alg = "FILE";
      load_alg_from_file(alg_file);
    end else begin
      if (!$value$plusargs("ALG=%s", alg)) alg = "MARCHCM";
      load_alg(alg);
    end
    if (!$value$plusargs("BACKGROUND=%h", background_mask)) background_mask = '0;

    repeat (4) @(negedge clk);

    // Outer mi loop: the FULL algorithm runs to completion against memory
    // mi before moving to mi+1 -- matches wrapper_template.j2's own
    // SHB_RUN/SHB_ADVANCE sequencer exactly (one full pass per memory,
    // never interleaved), not an inner-loop mux that would misrepresent
    // what the real shared controller actually does cycle by cycle.
    for (int mi = 0; mi < NUM_MEMORIES && !detected; mi++) begin
      for (int e = 0; e < nelem && !detected; e++) begin
        int a0, a1, st;
        if (prog[e].dir == 1) begin a0 = DEPTH-1; a1 = -1;    st = -1; end
        else                  begin a0 = 0;       a1 = DEPTH; st =  1; end
        for (int a = a0; a != a1 && !detected; a += st) begin
          for (int o = 0; o < prog[e].nops && !detected; o++) begin
            case (prog[e].ops[o])
              0: do_read (a, 1'b0, mi, e, o);
              1: do_read (a, 1'b1, mi, e, o);
              2: do_write(a, 1'b0, mi);
              3: do_write(a, 1'b1, mi);
              -1: do_write(a, a[0], mi);        // wc:  addr-parity write
              -2: do_write(a, ~a[0], mi);       // wcb: addr-parity-complement write
              -3: do_read (a, a[0], mi, e, o);  // rc:  addr-parity read
              -4: do_read (a, ~a[0], mi, e, o); // rcb: addr-parity-complement read
              default: if (prog[e].ops[o] >= 4) do_wait(prog[e].ops[o] - 4, mi);
            endcase
          end
        end
      end
    end

    if (detected)
      $display("RESULT DETECTED alg=%s elem=%0d op=%0d addr=%0d xor=%b mem=%0d",
               alg, det_elem, det_op, det_addr, det_xor, det_mem);
    else
      $display("RESULT ESCAPED alg=%s", alg);
    $finish;
  end

endmodule
