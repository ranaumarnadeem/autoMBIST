// march_engine_mp.sv
// Multi-port (num_ports=2) sibling of march_engine.sv -- NOT a modification of
// that file. Drives a fault_ram compiled with num_ports=2 (see
// fault_ram_template.sv.j2 / fault_ram_gen.render_fault_ram(num_ports=2)):
// two independent, explicit-suffix port buses
// (clk0/csb0/web0/wmask0/addr0/din0/dout0, clk1/csb1/web1/wmask1/addr1/din1/dout1).
//
//   +ALG_FILE=<file>   numeric element/op program (preferred; emitted by autombist)
//   +ALG=MATSP|MARCHCM|MARCHSS   built-in fallback for tool-free smoke tests
//                                (every op runs on port 0 -- these built-ins
//                                predate multi-port and are single-port programs)
//   +BACKGROUND=<hex>  DW-bit data-background mask (default 0 = solid 0/1,
//                      byte-identical to every campaign that omits it)
//   plus all fault_ram plusargs (+FAULTS, +FAULT_INDEX, +INIT, +FAULT_VERBOSE)
//
// AW/DW/WORDS_PER_ROW are top parameters so a driver can override at compile
// time (Verilator: -GAW=<n> -GDW=<n> -GWORDS_PER_ROW=<n>). WORDS_PER_ROW
// defaults to 1 (byte-identical to before it existed) and is forwarded
// straight through to fault_ram -- HSD (Half-Select Disturb, Workstream L)
// needs no other change here: its check lives inside fault_ram's write_op(),
// which this file already calls once per port against the SAME shared mem[]
// -- "physical row shared across both ports" falls out for free.
//
// Prints exactly one line beginning with RESULT:
//   RESULT DETECTED alg=<a> elem=<e> op=<o> addr=<n> xor=<bits>
//   RESULT ESCAPED  alg=<a>
//
// Numeric .alg line format (decimal, '#' comments), TWO accepted shapes:
//   plain    (pre-multi-port, byte-identical to march_engine.sv's format):
//     DIR NOPS OP0 OP1 OP2 OP3 OP4 OP5 OP6 OP7
//   extended (emitted by AlgSpec.to_numeric() when any op in the spec carries
//   a non-zero port tag; see alg_spec.py's Element.numeric_line()):
//     DIR NOPS OP0..OP7 PORT0..PORT7
//   DIR: 0=up 1=down 2=either    OP: 0=r0 1=r1 2=w0 3=w1  (padded with 0)
//   OP >= 4: wait/idle op, idle for (OP-4) clock cycles, no memory access
//   (see alg_spec.py's WAIT_BASE; for Data Retention Fault modeling). Like
//   every op, a wait executes once per address in the element's range -- an
//   element with a single wait op idles (OP-4)*DEPTH cycles total, not once.
//   PORT: 0 or 1, parallel to OP0..OP7 (padded with 0 -> port 0). A plain
//   line (no PORT columns) means every op in that element is on port 0,
//   exactly as march_engine.sv already assumes -- this engine parses BOTH
//   shapes so single-port .algc files (no port columns at all) parse
//   correctly through this engine too (see the 1-port sanity check in
//   tests/integration/test_march_engine_mp_sanity.py).
//
// Word background: under +BACKGROUND=<mask> (default 0, i.e. solid 0/1 as
// before), a nominal w0/r0 drives/expects `mask` and w1/r1 drives/expects
// `~mask` (see bg_value below, shared identically with march_engine.sv) --
// exposes intra-word coupling faults a uniform solid background cannot.

`timescale 1ns/1ps

module march_engine_mp #(
  parameter int AW = 8,
  parameter int DW = 8,
  parameter int WORDS_PER_ROW = 1
);

  localparam int DEPTH = 1 << AW;

  logic clk0 = 0;
  logic csb0 = 1, web0 = 1;
  logic [DW-1:0] wmask0 = '1;
  logic [AW-1:0] addr0 = '0;
  logic [DW-1:0] din0 = '0, dout0;

  logic clk1 = 0;
  logic csb1 = 1, web1 = 1;
  logic [DW-1:0] wmask1 = '1;
  logic [AW-1:0] addr1 = '0;
  logic [DW-1:0] din1 = '0, dout1;

  logic [DW-1:0] background_mask = '0;

  fault_ram #(.ADDR_WIDTH(AW), .DATA_WIDTH(DW), .WORDS_PER_ROW(WORDS_PER_ROW)) dut (
    .clk0(clk0), .csb0(csb0), .web0(web0), .wmask0(wmask0),
    .addr0(addr0), .din0(din0), .dout0(dout0),
    .clk1(clk1), .csb1(csb1), .web1(web1), .wmask1(wmask1),
    .addr1(addr1), .din1(din1), .dout1(dout1)
  );

  always #5 clk0 = ~clk0;
  always #5 clk1 = ~clk1;

  // Nominal value v (0/1) under the current data background: w0/r0 -> mask,
  // w1/r1 -> ~mask. mask=0 (the default) reduces to {DW{v}} exactly, so
  // every campaign that omits +BACKGROUND sees byte-identical behavior.
  function automatic logic [DW-1:0] bg_value(input bit v);
    bg_value = background_mask ^ {DW{v}};
  endfunction

  // op codes: 0=r0 1=r1 2=w0 3=w1 ; dir: 0=up 1=down 2=either(run up)
  // port: 0 or 1, parallel to ops.
  typedef struct {
    int dir;
    int nops;
    int ops[8];
    int ports[8];
  } elem_s;

  elem_s prog[16];
  int    nelem;
  string alg;
  string alg_file;

  function automatic void load_alg(string a);
    // Built-in fallback programs predate multi-port: every op is on port 0.
    case (a)
      "MATSP": begin // {either(w0); up(r0,w1); down(r1,w0)}   5n
        nelem = 3;
        prog[0] = '{dir:2, nops:1, ops:'{2,0,0,0,0,0,0,0}, ports:'{default:0}};
        prog[1] = '{dir:0, nops:2, ops:'{0,3,0,0,0,0,0,0}, ports:'{default:0}};
        prog[2] = '{dir:1, nops:2, ops:'{1,2,0,0,0,0,0,0}, ports:'{default:0}};
      end
      "MARCHCM": begin // March C-   10n
        nelem = 6;
        prog[0] = '{dir:2, nops:1, ops:'{2,0,0,0,0,0,0,0}, ports:'{default:0}};
        prog[1] = '{dir:0, nops:2, ops:'{0,3,0,0,0,0,0,0}, ports:'{default:0}};
        prog[2] = '{dir:0, nops:2, ops:'{1,2,0,0,0,0,0,0}, ports:'{default:0}};
        prog[3] = '{dir:1, nops:2, ops:'{0,3,0,0,0,0,0,0}, ports:'{default:0}};
        prog[4] = '{dir:1, nops:2, ops:'{1,2,0,0,0,0,0,0}, ports:'{default:0}};
        prog[5] = '{dir:2, nops:1, ops:'{0,0,0,0,0,0,0,0}, ports:'{default:0}};
      end
      "MARCHSS": begin // March SS   22n
        nelem = 6;
        prog[0] = '{dir:2, nops:1, ops:'{2,0,0,0,0,0,0,0}, ports:'{default:0}};
        prog[1] = '{dir:0, nops:5, ops:'{0,0,2,0,3,0,0,0}, ports:'{default:0}};
        prog[2] = '{dir:0, nops:5, ops:'{1,1,3,1,2,0,0,0}, ports:'{default:0}};
        prog[3] = '{dir:1, nops:5, ops:'{0,0,2,0,3,0,0,0}, ports:'{default:0}};
        prog[4] = '{dir:1, nops:5, ops:'{1,1,3,1,2,0,0,0}, ports:'{default:0}};
        prog[5] = '{dir:2, nops:1, ops:'{0,0,0,0,0,0,0,0}, ports:'{default:0}};
      end
      default: begin
        $display("FATAL: unknown +ALG=%s", a);
        $finish;
      end
    endcase
  endfunction

  // File-driven algorithm: numeric lines, EITHER shape:
  //   plain:    DIR NOPS OP0..OP7                  (10 fields)
  //   extended: DIR NOPS OP0..OP7 PORT0..PORT7     (18 fields)
  // A plain line means every op in that element is on port 0, matching
  // march_engine.sv's assumption exactly.
  //
  // Under Verilator (and per the IEEE 1800 $sscanf semantics it follows),
  // $sscanf returns the NEGATIVE of the format-item count on any format
  // conversion failure -- notably including "format asks for more items
  // than the string has" -- rather than the partial count of items it did
  // manage to convert (unlike libc scanf/sscanf). So a single 18-item
  // $sscanf attempted against a 10-field plain line returns a negative
  // value, NOT 10: this function must try the extended 18-item format
  // FIRST and only fall back to the plain 10-item format if that fails,
  // rather than inspecting a single call's return count.
  function automatic void load_alg_from_file(string fpath);
    int    fd, n;
    string line;
    int    d, nops;
    int    o0, o1, o2, o3, o4, o5, o6, o7;
    int    pt0, pt1, pt2, pt3, pt4, pt5, pt6, pt7;
    nelem = 0;
    fd = $fopen(fpath, "r");
    if (fd == 0) begin
      $display("FATAL: cannot open ALG_FILE %s", fpath);
      $finish;
    end
    while ($fgets(line, fd) != 0 && nelem < 16) begin
      if (line.substr(0,0) == "#") continue;
      o0=0; o1=0; o2=0; o3=0; o4=0; o5=0; o6=0; o7=0;
      pt0=0; pt1=0; pt2=0; pt3=0; pt4=0; pt5=0; pt6=0; pt7=0;

      // Try the extended (18-field) shape first.
      n = $sscanf(line, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
                  d, nops, o0, o1, o2, o3, o4, o5, o6, o7,
                  pt0, pt1, pt2, pt3, pt4, pt5, pt6, pt7);
      if (n == 18) begin
        prog[nelem].dir   = d;
        prog[nelem].nops  = nops;
        // Element-wise, not a '{...} assignment pattern: Verilator 5.020 (the
        // version Ubuntu 24.04's apt package ships) rejects an assignment
        // pattern targeting an unpacked array that is itself a struct field
        // (STRUCTSEL error), even though newer Verilator accepts it.
        prog[nelem].ops[0] = o0;
        prog[nelem].ops[1] = o1;
        prog[nelem].ops[2] = o2;
        prog[nelem].ops[3] = o3;
        prog[nelem].ops[4] = o4;
        prog[nelem].ops[5] = o5;
        prog[nelem].ops[6] = o6;
        prog[nelem].ops[7] = o7;
        prog[nelem].ports[0] = pt0;
        prog[nelem].ports[1] = pt1;
        prog[nelem].ports[2] = pt2;
        prog[nelem].ports[3] = pt3;
        prog[nelem].ports[4] = pt4;
        prog[nelem].ports[5] = pt5;
        prog[nelem].ports[6] = pt6;
        prog[nelem].ports[7] = pt7;
        nelem++;
        continue;
      end

      // Fall back to the plain (10-field) shape -- every op on port 0.
      o0=0; o1=0; o2=0; o3=0; o4=0; o5=0; o6=0; o7=0;
      n = $sscanf(line, "%d %d %d %d %d %d %d %d %d %d",
                  d, nops, o0, o1, o2, o3, o4, o5, o6, o7);
      if (n < 2) continue;
      prog[nelem].dir   = d;
      prog[nelem].nops  = nops;
      prog[nelem].ops[0] = o0;
      prog[nelem].ops[1] = o1;
      prog[nelem].ops[2] = o2;
      prog[nelem].ops[3] = o3;
      prog[nelem].ops[4] = o4;
      prog[nelem].ops[5] = o5;
      prog[nelem].ops[6] = o6;
      prog[nelem].ops[7] = o7;
      prog[nelem].ports[0] = 0;
      prog[nelem].ports[1] = 0;
      prog[nelem].ports[2] = 0;
      prog[nelem].ports[3] = 0;
      prog[nelem].ports[4] = 0;
      prog[nelem].ports[5] = 0;
      prog[nelem].ports[6] = 0;
      prog[nelem].ports[7] = 0;
      nelem++;
    end
    $fclose(fd);
    if (nelem == 0) begin
      $display("FATAL: no elements parsed from ALG_FILE %s", fpath);
      $finish;
    end
  endfunction

  task automatic do_write(input int a, input bit v, input int port);
    if (port == 0) begin
      @(negedge clk0);
      csb0 = 0; web0 = 0; addr0 = a[AW-1:0]; din0 = bg_value(v);
      @(posedge clk0);
      @(negedge clk0);
      csb0 = 1; web0 = 1;
    end else begin
      @(negedge clk1);
      csb1 = 0; web1 = 0; addr1 = a[AW-1:0]; din1 = bg_value(v);
      @(posedge clk1);
      @(negedge clk1);
      csb1 = 1; web1 = 1;
    end
  endtask

  // Idle for n full clock periods; both ports' csb stay deasserted throughout
  // -- never touches either DUT port, dout0/dout1, or the detected/det_*
  // bookkeeping. clk0/clk1 toggle in lockstep (both `#5` half-period), so
  // clk0 alone is a sufficient timing reference -- same negedge-anchored
  // one-period-per-iteration idiom as march_engine.sv's do_wait.
  task automatic do_wait(input int n);
    repeat (n) begin
      @(negedge clk0);
      csb0 = 1; csb1 = 1;
      @(posedge clk0);
      @(negedge clk0);
    end
  endtask

  int det_elem, det_op, det_addr;
  logic [DW-1:0] det_xor;
  bit detected = 0;

  task automatic do_read(input int a, input bit v, input int port,
                         input int ei, input int oi);
    if (port == 0) begin
      @(negedge clk0);
      csb0 = 0; web0 = 1; addr0 = a[AW-1:0];
      @(posedge clk0);        // dout0 updates here
      @(negedge clk0);
      csb0 = 1;
      if (dout0 !== bg_value(v) && !detected) begin
        detected = 1;
        det_elem = ei; det_op = oi; det_addr = a;
        det_xor  = dout0 ^ bg_value(v);
      end
    end else begin
      @(negedge clk1);
      csb1 = 0; web1 = 1; addr1 = a[AW-1:0];
      @(posedge clk1);        // dout1 updates here
      @(negedge clk1);
      csb1 = 1;
      if (dout1 !== bg_value(v) && !detected) begin
        detected = 1;
        det_elem = ei; det_op = oi; det_addr = a;
        det_xor  = dout1 ^ bg_value(v);
      end
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

    repeat (4) @(negedge clk0);

    for (int e = 0; e < nelem && !detected; e++) begin
      int a0, a1, st;
      if (prog[e].dir == 1) begin a0 = DEPTH-1; a1 = -1;    st = -1; end
      else                  begin a0 = 0;       a1 = DEPTH; st =  1; end
      for (int a = a0; a != a1 && !detected; a += st) begin
        for (int o = 0; o < prog[e].nops && !detected; o++) begin
          case (prog[e].ops[o])
            0: do_read (a, 1'b0, prog[e].ports[o], e, o);
            1: do_read (a, 1'b1, prog[e].ports[o], e, o);
            2: do_write(a, 1'b0, prog[e].ports[o]);
            3: do_write(a, 1'b1, prog[e].ports[o]);
            default: if (prog[e].ops[o] >= 4) do_wait(prog[e].ops[o] - 4);
          endcase
        end
      end
    end

    if (detected)
      $display("RESULT DETECTED alg=%s elem=%0d op=%0d addr=%0d xor=%b",
               alg, det_elem, det_op, det_addr, det_xor);
    else
      $display("RESULT ESCAPED alg=%s", alg);
    $finish;
  end

endmodule
