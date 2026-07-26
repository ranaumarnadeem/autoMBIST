// march_engine.sv
// File-driven March algorithm runner against fault_ram (was march_tb.sv).
//
//   +ALG_FILE=<file>   numeric element/op program (preferred; emitted by autombist)
//   +ALG=MATSP|MARCHCM|MARCHSS   built-in fallback for tool-free smoke tests
//   plus all fault_ram plusargs (+FAULTS, +FAULT_INDEX, +INIT, +FAULT_VERBOSE)
//
// AW/DW are top parameters so a driver can override at compile time
// (Verilator: -GAW=<n> -GDW=<n>).
//
// Prints exactly one line beginning with RESULT:
//   RESULT DETECTED alg=<a> elem=<e> op=<o> addr=<n> xor=<bits>
//   RESULT ESCAPED  alg=<a>
//
// Numeric .alg line format (decimal, '#' comments):
//   DIR NOPS OP0 OP1 OP2 OP3 OP4 OP5 OP6 OP7
//   DIR: 0=up 1=down 2=either    OP: 0=r0 1=r1 2=w0 3=w1  (padded with 0)
//
// Word background is solid 0 / solid 1. Intra-word coupling faults need data
// backgrounds and are not exercised here; place coupled pairs in different
// words, same bit lane (see faults.example.txt).

`timescale 1ns/1ps

module march_engine #(
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

  // op codes: 0=r0 1=r1 2=w0 3=w1 ; dir: 0=up 1=down 2=either(run up)
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
        prog[0] = '{dir:2, nops:1, ops:'{2,0,0,0,0,0,0,0}};
        prog[1] = '{dir:0, nops:2, ops:'{0,3,0,0,0,0,0,0}};
        prog[2] = '{dir:1, nops:2, ops:'{1,2,0,0,0,0,0,0}};
      end
      "MARCHCM": begin // March C-   10n
        nelem = 6;
        prog[0] = '{dir:2, nops:1, ops:'{2,0,0,0,0,0,0,0}};
        prog[1] = '{dir:0, nops:2, ops:'{0,3,0,0,0,0,0,0}};
        prog[2] = '{dir:0, nops:2, ops:'{1,2,0,0,0,0,0,0}};
        prog[3] = '{dir:1, nops:2, ops:'{0,3,0,0,0,0,0,0}};
        prog[4] = '{dir:1, nops:2, ops:'{1,2,0,0,0,0,0,0}};
        prog[5] = '{dir:2, nops:1, ops:'{0,0,0,0,0,0,0,0}};
      end
      "MARCHSS": begin // March SS   22n
        nelem = 6;
        prog[0] = '{dir:2, nops:1, ops:'{2,0,0,0,0,0,0,0}};
        prog[1] = '{dir:0, nops:5, ops:'{0,0,2,0,3,0,0,0}};
        prog[2] = '{dir:0, nops:5, ops:'{1,1,3,1,2,0,0,0}};
        prog[3] = '{dir:1, nops:5, ops:'{0,0,2,0,3,0,0,0}};
        prog[4] = '{dir:1, nops:5, ops:'{1,1,3,1,2,0,0,0}};
        prog[5] = '{dir:2, nops:1, ops:'{0,0,0,0,0,0,0,0}};
      end
      default: begin
        $display("FATAL: unknown +ALG=%s", a);
        $finish;
      end
    endcase
  endfunction

  // File-driven algorithm: numeric lines "DIR NOPS OP0..OP7".
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
      nelem++;
    end
    $fclose(fd);
    if (nelem == 0) begin
      $display("FATAL: no elements parsed from ALG_FILE %s", fpath);
      $finish;
    end
  endfunction

  task automatic do_write(input int a, input bit v);
    @(negedge clk);
    csb = 0; web = 0; addr = a[AW-1:0]; din = {DW{v}};
    @(posedge clk);
    @(negedge clk);
    csb = 1; web = 1;
  endtask

  int det_elem, det_op, det_addr;
  logic [DW-1:0] det_xor;
  bit detected = 0;

  task automatic do_read(input int a, input bit v,
                         input int ei, input int oi);
    @(negedge clk);
    csb = 0; web = 1; addr = a[AW-1:0];
    @(posedge clk);        // dout updates here
    @(negedge clk);
    csb = 1;
    if (dout !== {DW{v}} && !detected) begin
      detected = 1;
      det_elem = ei; det_op = oi; det_addr = a;
      det_xor  = dout ^ {DW{v}};
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

    repeat (4) @(negedge clk);

    for (int e = 0; e < nelem && !detected; e++) begin
      int a0, a1, st;
      if (prog[e].dir == 1) begin a0 = DEPTH-1; a1 = -1;    st = -1; end
      else                  begin a0 = 0;       a1 = DEPTH; st =  1; end
      for (int a = a0; a != a1 && !detected; a += st) begin
        for (int o = 0; o < prog[e].nops && !detected; o++) begin
          case (prog[e].ops[o])
            0: do_read (a, 1'b0, e, o);
            1: do_read (a, 1'b1, e, o);
            2: do_write(a, 1'b0);
            3: do_write(a, 1'b1);
            default: ;
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
