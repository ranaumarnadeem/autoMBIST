// fault_ram.sv
// Fault-injectable behavioral RAM for MBIST algorithm validation.
//
// Single-port synchronous RAM, registered dout, 1-cycle read latency.
// Active-low control (csb, web), bit-level write mask.
//
// Fault list:   +FAULTS=<file>       (omit for golden run)
// Single fault: +FAULT_INDEX=<n>     (0-based line among parsed faults; -1 = all, default)
// Init value:   +INIT=<0|1>          (default 1, see README)
// Verbose:      +FAULT_VERBOSE       (per-fault activation counts at end of sim)
//
// Fault line format (decimal, '#' comments):
//   TYPE  VADDR VBIT  AADDR ABIT  P0 P1
// See README.md for per-type semantics. DRF reuses P0 as an idle-cycle-count
// threshold (P1/AADDR/ABIT unused, write 0): a DRF corrupts the victim bit
// once P0+1 idle clock edges have elapsed since its last write, with no
// write in between -- see the idle-cycle-tracking always_ff block below for
// why it's P0+1, not P0 (measured directly in simulation, not assumed). HSD
// reuses P0 as the disturbed-toward polarity (0 or 1; AADDR/ABIT/P1 unused,
// write 0): any write to a DIFFERENT address sharing the victim's physical
// row (row(addr) = addr / WORDS_PER_ROW) forces the victim bit toward P0 --
// see the HSD check inside write_op() below and engine/README.md.

`timescale 1ns/1ps

module fault_ram #(
  parameter int ADDR_WIDTH = 8,
  parameter int DATA_WIDTH = 8,
  parameter int DEPTH      = 1 << ADDR_WIDTH,
  parameter int WORDS_PER_ROW = 1   // HSD (Workstream L): row(addr) = addr /
                                      // WORDS_PER_ROW. Default 1 -> row(addr)
                                      // = addr for every address, so "a
                                      // different address in the same row" is
                                      // mathematically unsatisfiable and HSD
                                      // is provably inert -- see README.md.
)(
  input  logic                    clk,
  input  logic                    csb,    // active low chip select
  input  logic                    web,    // active low write enable (0 = write, 1 = read)
  input  logic [DATA_WIDTH-1:0]   wmask,  // bit-level write mask (1 = write this bit)
  input  logic [ADDR_WIDTH-1:0]   addr,
  input  logic [DATA_WIDTH-1:0]   din,
  output logic [DATA_WIDTH-1:0]   dout
);

  // ---------------- fault type codes ----------------
  localparam int T_SA0=0,  T_SA1=1,  T_TF0=2,  T_TF1=3;
  localparam int T_WDF0=4, T_WDF1=5, T_RDF0=6, T_RDF1=7;
  localparam int T_DRDF0=8,T_DRDF1=9,T_IRF0=10,T_IRF1=11;
  localparam int T_SOF=12, T_AFNA=13,T_AFAL=14;
  localparam int T_CFIN=15,T_CFID=16,T_CFST=17,T_CFDS=18;
  localparam int T_DRF=19;   // Data Retention Fault (Workstream K) -- see the
                              // idle-cycle-tracking always_ff block below.
                              // KEPT IN SYNC MANUALLY with
                              // templates/fault_ram_template.sv.j2's DRF
                              // block -- no automated equivalence check
                              // between the two exists.
  localparam int T_HSD=20;   // Half-Select Disturb (Workstream L) -- see the
                              // row-membership check inside write_op() below.
                              // KEPT IN SYNC MANUALLY with
                              // templates/fault_ram_template.sv.j2's HSD
                              // check -- same caveat as T_DRF above.

  typedef struct {
    int t;      // type code
    int va, vb; // victim address / bit
    int aa, ab; // aggressor address / bit (coupling, alias target)
    int p0, p1; // type-specific parameters
    int hits;   // activation count
  } fault_s;

  fault_s FQ[$];
  string  FNAME[$];   // parallel: original type string for reporting

  logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

  // ---------------- fault list loading ----------------
  function automatic int type_code(string s);
    if (s == "SA0")      return T_SA0;
    if (s == "SA1")      return T_SA1;
    if (s == "TF0")      return T_TF0;
    if (s == "TF1")      return T_TF1;
    if (s == "WDF0")     return T_WDF0;
    if (s == "WDF1")     return T_WDF1;
    if (s == "RDF0")     return T_RDF0;
    if (s == "RDF1")     return T_RDF1;
    if (s == "DRDF0")    return T_DRDF0;
    if (s == "DRDF1")    return T_DRDF1;
    if (s == "IRF0")     return T_IRF0;
    if (s == "IRF1")     return T_IRF1;
    if (s == "SOF")      return T_SOF;
    if (s == "AF_NOACC") return T_AFNA;
    if (s == "AF_ALIAS") return T_AFAL;
    if (s == "CFIN")     return T_CFIN;
    if (s == "CFID")     return T_CFID;
    if (s == "CFST")     return T_CFST;
    if (s == "CFDS")     return T_CFDS;
    if (s == "DRF")      return T_DRF;
    if (s == "HSD")      return T_HSD;
    return -1;
  endfunction

  int init_val;

  initial begin
    string  fpath, line, ts;
    int     fd, n, idx, sel;
    fault_s f;

    if (!$value$plusargs("INIT=%d", init_val)) init_val = 1;
    for (int a = 0; a < DEPTH; a++) mem[a] = {DATA_WIDTH{init_val[0]}};
    dout = '0;

    if ($value$plusargs("FAULTS=%s", fpath)) begin
      fd = $fopen(fpath, "r");
      if (fd == 0) begin
        $display("FATAL: cannot open fault file %s", fpath);
        $finish;
      end
      idx = 0;
      while ($fgets(line, fd) != 0) begin
        n = $sscanf(line, "%s %d %d %d %d %d %d",
                    ts, f.va, f.vb, f.aa, f.ab, f.p0, f.p1);
        if (n < 1 || ts.substr(0,0) == "#") continue;
        f.t = type_code(ts);
        if (f.t < 0) begin
          $display("FATAL: unknown fault type '%s' (line %0d)", ts, idx);
          $finish;
        end
        if (n < 7) begin
          $display("FATAL: fault line %0d has %0d fields, need 7", idx, n);
          $finish;
        end
        f.hits = 0;
        FQ.push_back(f);
        FNAME.push_back(ts);
        idx++;
      end
      $fclose(fd);

      if (!$value$plusargs("FAULT_INDEX=%d", sel)) sel = -1;
      if (sel >= 0) begin
        if (sel >= FQ.size()) begin
          $display("FATAL: FAULT_INDEX %0d out of range (%0d faults)", sel, FQ.size());
          $finish;
        end
        f = FQ[sel];
        ts = FNAME[sel];
        FQ.delete(); FNAME.delete();
        FQ.push_back(f); FNAME.push_back(ts);
        $display("FAULT_LOADED idx=%0d type=%s v=%0d.%0d a=%0d.%0d p0=%0d p1=%0d",
                 sel, ts, f.va, f.vb, f.aa, f.ab, f.p0, f.p1);
      end else begin
        for (int i = 0; i < FQ.size(); i++)
          $display("FAULT_LOADED idx=%0d type=%s v=%0d.%0d a=%0d.%0d p0=%0d p1=%0d",
                   i, FNAME[i], FQ[i].va, FQ[i].vb, FQ[i].aa, FQ[i].ab, FQ[i].p0, FQ[i].p1);
      end
      // Apply stuck faults to the initial state as well.
      clamp_static();
    end
  end

  // ---------------- static clamps: SAF, CFST ----------------
  // SAF holds the victim bit at its stuck value at all times.
  // CFST forces the victim bit to p1 while the aggressor bit holds state p0.
  function automatic void clamp_static();
    foreach (FQ[i]) begin
      case (FQ[i].t)
        T_SA0: if (mem[FQ[i].va][FQ[i].vb] != 1'b0) begin mem[FQ[i].va][FQ[i].vb] = 1'b0; FQ[i].hits++; end
        T_SA1: if (mem[FQ[i].va][FQ[i].vb] != 1'b1) begin mem[FQ[i].va][FQ[i].vb] = 1'b1; FQ[i].hits++; end
        T_CFST: if (mem[FQ[i].aa][FQ[i].ab] == FQ[i].p0[0] &&
                    mem[FQ[i].va][FQ[i].vb] != FQ[i].p1[0]) begin
                  mem[FQ[i].va][FQ[i].vb] = FQ[i].p1[0];
                  FQ[i].hits++;
                end
        default: ;
      endcase
    end
  endfunction

  function automatic bit dir_match(int p, bit up, bit dn);
    // p: 0 = up (0->1), 1 = down (1->0), 2 = either
    return (p == 2 && (up || dn)) || (p == 0 && up) || (p == 1 && dn);
  endfunction

  // ---------------- write path ----------------
  function automatic void write_op(input logic [ADDR_WIDTH-1:0] a,
                                   input logic [DATA_WIDTH-1:0] d,
                                   input logic [DATA_WIDTH-1:0] m);
    int ea;
    logic [DATA_WIDTH-1:0] old, nxt;
    ea = int'(a);

    // Address decoder faults act on the whole word.
    foreach (FQ[i]) begin
      if (FQ[i].va != ea) continue;
      if (FQ[i].t == T_AFNA) begin FQ[i].hits++; return; end       // write reaches no cell
      if (FQ[i].t == T_AFAL) begin FQ[i].hits++; ea = FQ[i].aa; end // write lands on alias target
    end

    old = mem[ea];
    nxt = old;

    // Per-bit cell write faults on the effective address.
    for (int b = 0; b < DATA_WIDTH; b++) begin
      if (!m[b]) continue;
      nxt[b] = d[b];
      foreach (FQ[i]) begin
        if (FQ[i].va != ea || FQ[i].vb != b) continue;
        case (FQ[i].t)
          T_TF0:  if (old[b] == 1'b0 && d[b] == 1'b1) begin nxt[b] = 1'b0;   FQ[i].hits++; end
          T_TF1:  if (old[b] == 1'b1 && d[b] == 1'b0) begin nxt[b] = 1'b1;   FQ[i].hits++; end
          T_WDF0: if (old[b] == 1'b0 && d[b] == 1'b0) begin nxt[b] = 1'b1;   FQ[i].hits++; end
          T_WDF1: if (old[b] == 1'b1 && d[b] == 1'b1) begin nxt[b] = 1'b0;   FQ[i].hits++; end
          T_SOF:  begin nxt[b] = old[b]; FQ[i].hits++; end                    // cell inaccessible
          default: ;
        endcase
      end
    end

    mem[ea] = nxt;

    // Aggressor-side coupling triggers caused by this write.
    for (int b = 0; b < DATA_WIDTH; b++) begin
      bit up, dn, nt0, nt1;
      if (!m[b]) continue;
      up  = (old[b] == 1'b0) && (nxt[b] == 1'b1);
      dn  = (old[b] == 1'b1) && (nxt[b] == 1'b0);
      nt0 = (old[b] == 1'b0) && (d[b]   == 1'b0);   // attempted non-transition w0
      nt1 = (old[b] == 1'b1) && (d[b]   == 1'b1);   // attempted non-transition w1
      foreach (FQ[i]) begin
        if (FQ[i].aa != ea || FQ[i].ab != b) continue;
        case (FQ[i].t)
          T_CFIN: if (dir_match(FQ[i].p0, up, dn)) begin
                    mem[FQ[i].va][FQ[i].vb] = ~mem[FQ[i].va][FQ[i].vb];
                    FQ[i].hits++;
                  end
          T_CFID: if (dir_match(FQ[i].p0, up, dn)) begin
                    mem[FQ[i].va][FQ[i].vb] = FQ[i].p1[0];
                    FQ[i].hits++;
                  end
          T_CFDS: if ((FQ[i].p0 == 2 && nt0) || (FQ[i].p0 == 3 && nt1)) begin
                    mem[FQ[i].va][FQ[i].vb] = ~mem[FQ[i].va][FQ[i].vb];
                    FQ[i].hits++;
                  end
          default: ;
        endcase
      end
    end

    // Half-select disturb (HSD, Workstream L): a write to any OTHER address
    // sharing the victim's physical row forces the victim toward FQ[i].p0
    // (the disturbed-toward polarity) -- NOT an invert. Real half-select
    // physics pulls a half-selected cell toward the array's undriven/
    // precharge polarity, not toward "whatever it currently isn't"; a cell
    // already at that polarity is not disturbed further (the != guard below
    // also means this only counts as an activation when it actually changes
    // something, the same idiom SA0/SA1 already use in clamp_static()).
    // A SINGLE per-fault check for the whole word, deliberately NOT nested
    // in either per-bit loop above: word-line assertion is a per-WORD event
    // independent of which bits THIS write's own wmask happens to touch, so
    // this must fire even when m=='0 -- nesting it in a `for (int b...)`
    // loop would multi-trigger once per set mask bit instead of once per
    // qualifying write. Keyed on `ea` (post-AF_ALIAS effective address, not
    // the raw port address), matching every other aggressor check above.
    //
    // A same-row disturb only survives to be OBSERVED if it happens after the
    // victim's own most recent direct write in the traversal -- a later
    // direct write to the victim always overwrites an earlier disturb (the
    // per-bit victim loop above computes nxt unconditionally from d, with no
    // HSD-awareness, so mem[ea]=nxt at the direct-write cycle simply replaces
    // whatever value was there, disturbed or not). Confirmed directly in
    // simulation, not assumed: for a depth-4, words_per_row=2 memory, an HSD
    // fault at victim=addr3 (row {2,3}) with row-mate addr2 ESCAPES an
    // "up w0 / up r0" spec (addr2 is written BEFORE addr3's own direct write
    // in ascending order, so the disturb is overwritten before it's ever
    // read), while the identical setup at victim=addr0 (row-mate addr1,
    // written AFTER addr0's direct write) DETECTS. This is real, expected
    // physics (a disturb followed by a legitimate rewrite is gone), not a
    // bug -- see engine/README.md.
    //
    // Runs before the trailing clamp_static() call below, same precedence
    // CFIN/CFID/CFDS already have relative to it.
    foreach (FQ[i]) begin
      if (FQ[i].t != T_HSD) continue;
      if (ea == FQ[i].va) continue;                                       // direct write to the victim itself
      if ((ea / WORDS_PER_ROW) != (FQ[i].va / WORDS_PER_ROW)) continue;    // different physical row
      if (mem[FQ[i].va][FQ[i].vb] != FQ[i].p0[0]) begin
        mem[FQ[i].va][FQ[i].vb] = FQ[i].p0[0];
        FQ[i].hits++;
      end
    end

    clamp_static();
  endfunction

  // ---------------- read path ----------------
  function automatic logic [DATA_WIDTH-1:0] read_op(input logic [ADDR_WIDTH-1:0] a);
    int ea;
    logic [DATA_WIDTH-1:0] old, rv;
    ea = int'(a);

    foreach (FQ[i]) begin
      if (FQ[i].va != ea) continue;
      if (FQ[i].t == T_AFNA) begin
        FQ[i].hits++;
        return {DATA_WIDTH{FQ[i].p0[0]}};   // undriven read returns fixed value p0
      end
      if (FQ[i].t == T_AFAL) begin FQ[i].hits++; ea = FQ[i].aa; end
    end

    clamp_static();       // CFST state must be visible before the read
    old = mem[ea];
    rv  = old;

    for (int b = 0; b < DATA_WIDTH; b++) begin
      foreach (FQ[i]) begin
        if (FQ[i].va != ea || FQ[i].vb != b) continue;
        case (FQ[i].t)
          T_IRF0:  if (old[b] == 1'b0) begin rv[b] = 1'b1;                       FQ[i].hits++; end
          T_IRF1:  if (old[b] == 1'b1) begin rv[b] = 1'b0;                       FQ[i].hits++; end
          T_RDF0:  if (old[b] == 1'b0) begin mem[ea][b] = 1'b1; rv[b] = 1'b1;    FQ[i].hits++; end
          T_RDF1:  if (old[b] == 1'b1) begin mem[ea][b] = 1'b0; rv[b] = 1'b0;    FQ[i].hits++; end
          T_DRDF0: if (old[b] == 1'b0) begin mem[ea][b] = 1'b1; rv[b] = 1'b0;    FQ[i].hits++; end
          T_DRDF1: if (old[b] == 1'b1) begin mem[ea][b] = 1'b0; rv[b] = 1'b1;    FQ[i].hits++; end
          T_SOF:   begin rv[b] = dout[b]; FQ[i].hits++; end   // output keeper returns stale data
          default: ;
        endcase
      end
    end

    // Read-triggered coupling disturbs.
    for (int b = 0; b < DATA_WIDTH; b++) begin
      foreach (FQ[i]) begin
        if (FQ[i].t != T_CFDS || FQ[i].aa != ea || FQ[i].ab != b) continue;
        if ((FQ[i].p0 == 0 && old[b] == 1'b0) ||
            (FQ[i].p0 == 1 && old[b] == 1'b1) ||
            (FQ[i].p0 == 4)) begin
          mem[FQ[i].va][FQ[i].vb] = ~mem[FQ[i].va][FQ[i].vb];
          FQ[i].hits++;
        end
      end
    end

    clamp_static();
    return rv;
  endfunction

  // ---------------- port behavior ----------------
  always @(posedge clk) begin
    if (!csb) begin
      if (!web) write_op(addr, din, wmask);
      else      dout <= read_op(addr);
    end
  end

  // ---------------- Data Retention Fault (DRF) idle-cycle tracking ----------------
  // DRF is sensitized by ELAPSED IDLE TIME on the victim cell, not by any
  // read/write access -- the engine's first access-independent state (runs
  // unconditionally every clock, regardless of csb, so a wait op -- which
  // never asserts csb low -- still advances it). Exactly one DRF fault is
  // modeled per run: FQ[0], matching the +FAULT_INDEX single-active-fault
  // invariant every real campaign uses (algo_engine.run_algo_campaign always
  // passes +FAULT_INDEX=<i> per fault). Running WITHOUT +FAULT_INDEX
  // (all-faults mode) only ever tracks FQ[0] -- if the very FIRST fault in
  // the whole file (of any type, not merely the first DRF-typed line) isn't
  // itself a DRF, no DRF entry anywhere in the file is modeled at all in
  // that mode. See the `final` block below for the corresponding
  // FAULT_VERBOSE caveat.
  //
  // The counter runs from simulation time 0 (power-up), NOT from a first
  // arming write -- there is no "wait for the first write before counting"
  // gate. With a small P0, a campaign whose algorithm doesn't write the
  // victim address promptly (e.g. it writes other addresses or reads first)
  // can corrupt the cell before the intended write/wait/read sequence even
  // runs. A campaign exercising DRF should write the victim address
  // explicitly before relying on any wait-then-read pattern.
  //
  // The idle-cycle comparison below checks drf_idle_count's value from the
  // PRIOR qualifying edge, before that edge's own increment -- so corruption
  // actually commits on the (P0+1)-th qualifying edge since the last write,
  // not the P0-th (confirmed directly in simulation, not derived from the
  // code shape alone; see the fault-list-format header comment above and
  // README.md's "Idle/wait op and Data Retention Fault (DRF)" section,
  // which both state P0+1 for this reason).
  //
  // Race analysis (resolved by construction, not by assuming simulator event
  // ordering is forgiving):
  //   1. The if/else below makes the reset branch and the fire-check branch
  //      mutually exclusive on any given edge, so a write to the victim can
  //      never coincide with a corruption on the identical cycle.
  //   2. A read landing on the exact corrupting edge observes the
  //      pre-corruption value that cycle (read_op's blocking read samples
  //      mem[] in the active region, before this block's non-blocking
  //      commit) -- corruption becomes visible on the NEXT access. This is a
  //      deliberate characteristic, analogous to the existing documented
  //      "1-cycle read latency" on dout, not a bug.
  //   3. All memory-affecting assignments here are non-blocking (<=),
  //      consistent with the rest of this file's clocked-block style.
  //
  // drf_idle_count/drf_corrupted/drf_hits are declaration-initialized (not
  // assigned from the `initial` block above), and drf_hits is a DEDICATED
  // counter never folded into FQ[0].hits directly -- this keeps every piece
  // of DRF-tracking state single-writer (this always_ff block alone),
  // distinct from FQ[i].hits, which stays owned entirely by the pre-existing
  // always @(posedge clk) block via write_op/read_op/clamp_static. See the
  // `final` block below for how drf_hits is folded into the FAULT_HITS
  // report.
  int drf_idle_count = 0;
  bit drf_corrupted  = 0;   // latches for one idle window; cleared on the
                             // next write to the victim cell, re-arming DRF
  int drf_hits = 0;

  always_ff @(posedge clk) begin
    if (FQ.size() > 0 && FQ[0].t == T_DRF) begin
      if (!csb && !web && int'(addr) == FQ[0].va) begin
        drf_idle_count <= 0;
        drf_corrupted  <= 0;
      end else if (!drf_corrupted) begin
        if (drf_idle_count >= FQ[0].p0) begin
          mem[FQ[0].va][FQ[0].vb] <= ~mem[FQ[0].va][FQ[0].vb];
          drf_hits      <= drf_hits + 1;
          drf_corrupted <= 1;
        end else begin
          drf_idle_count <= drf_idle_count + 1;
        end
      end
    end
  end

  final begin
    if ($test$plusargs("FAULT_VERBOSE"))
      foreach (FQ[i])
        // DRF's activation count lives in the dedicated drf_hits counter
        // (see the idle-cycle-tracking always_ff block), not FQ[i].hits --
        // folded in here rather than written into FQ[i].hits directly, to
        // avoid a second clocked process touching the same struct field.
        // Only FQ[0] is EVER tracked by that block (see its header comment),
        // so a DRF entry at any other index is structurally inert and must
        // report 0, not drf_hits -- otherwise an untracked DRF entry would
        // misleadingly appear to share FQ[0]'s real activation count.
        $display("FAULT_HITS idx=%0d type=%s activations=%0d",
                 i, FNAME[i], (FQ[i].t == T_DRF) ? (i == 0 ? drf_hits : 0) : FQ[i].hits);
  end

endmodule
