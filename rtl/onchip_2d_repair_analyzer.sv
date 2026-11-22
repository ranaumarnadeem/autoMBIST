`timescale 1ns/1ps
// On-chip 2D (row + column) repair analyzer -- a CAM-style registrar,
// structurally parallel to rtl/onchip_row_repair_analyzer.sv (same row-CAM
// logic, reused verbatim), but additionally streams a per-cycle
// fail_bitmask and decides, for each newly-observed failing bit position,
// whether to spend a ROW spare or a COLUMN spare on it.
//
// This is a SINGLE-PASS, no-lookahead, no-backtracking APPROXIMATION of
// repair.bira.analyze()'s exact two-phase algorithm (a degree-based
// must-repair fixed point, THEN true branch-and-bound search over whatever
// is left ambiguous -- see bira.py; the backtracking half is NP-complete,
// Kuo & Fuchs 1987, and needs the WHOLE fail set buffered to search over,
// which this module deliberately does not do). It is NOT a hardware
// implementation of BIRA -- it is a heuristic designed to behave reasonably
// under the constraint of deciding each observation immediately, as the
// march algorithm streams it, with no ability to revisit an earlier
// decision.
//
// Decision rule, per newly-observed (row, bit) pair (row = fail_addr,
// walking fail_bitmask's set bits in ascending index order):
//   1. Row already has a live row claim -> already covered, no action for
//      ANY bit in this word (the whole row is being redirected to a spare
//      row; individual bit badness at this row no longer matters).
//   2. Bit already has a live column claim -> already covered universally
//      (column steering is row-address-independent, see
//      rtl/repair_remap_col.sv), no action.
//   3. Bit has been seen failing before (on some earlier, not-already-
//      row-covered row) -> genuine evidence of a column-level defect:
//      claim a free column slot NOW if one exists. If none is free, fall
//      back to wanting a row claim for the CURRENT row instead of giving
//      up -- see "Deliberate, documented properties" below, this is the
//      difference between a real fallback and a false unrepairable.
//   4. First-ever sighting of this bit -> default to wanting a row claim
//      ("row wins ties", matching repair.bira.analyze()'s own tie-break
//      for genuinely first-sight cases) and remember the bit as seen.
// Every bit in the word that "wants a row" (from rule 3's fallback or
// rule 4) OR-reduces into a SINGLE word-level row-claim attempt, applied
// to the row CAM exactly once -- never once per bit, which would
// multiply-drive the same registered row slot.
//
// Lenient: when a word wants a row and none is free, nothing is decided. A
// later read of the same word (the march reads every word several times) can
// still claim columns for its bits, since they now count as seen, and the
// sequencer's verify pass decides whether the repair holds. So `unrepairable`
// is never asserted by this module. (Until 2026-10 the first row request with
// no row left set a sticky unrepairable flag and skipped the verify pass,
// even when the very next read would have covered the bit with a free column:
// with 3 spare rows and 3 spare columns, 4 isolated single cells failed.)
//
// Deferred mode (DEFERRED = 1, with the sequencer's TWO_PASS): two analyze
// passes. In pass one (`defer` high) a bit takes a column only when it fails
// at a DIFFERENT row from the one it first failed at (rule 3 tightened), a
// word takes a row only when two or more of its failing bits are left
// uncovered, and a lone first sighting is remembered (first_seen/first_row)
// instead of taking a row. Pass two (`defer` low) applies the greedy rules
// above to what pass one left uncovered, through pass one's latched remap,
// with every remembered bit counting as seen. This repairs most of the fail
// maps the greedy single pass gives up on (src/autombist/yieldsim/onchip_model.py
// models both modes decision for decision; docs/source/yield-analysis.md has
// the measured rates), at the cost of DATA_WIDTH x (ADDR_WIDTH + 1) flops and
// one more analyze march.
//
// Output packing: row_repair_en/faulty_row_addr match
// rtl/repair_remap_row.sv exactly (same convention as
// onchip_row_repair_analyzer.sv). col_repair_en/faulty_bit match
// rtl/repair_remap_col.sv exactly -- faulty_bit is a GLOBAL column index
// (the logical bit lane 0..DATA_WIDTH-1, or with column muxing the physical
// column, see below), the same convention
// src/autombist/repair/bisr.py::encode_repair already produces for the
// tester-driven flow.
//
// Deliberate, documented properties (not bugs):
//   * This is a real, disclosed approximation, not "one extra spare in the
//     worst case": because there is no lookahead or backtracking, a bit
//     that recurs across multiple rows AFTER its first row has already
//     consumed a row spare can cost strictly more total spares than
//     repair.bira.analyze()'s exact solution would have needed for the
//     IDENTICAL fault set and budget -- and in some visitation orders, this
//     can exhaust the row+column budget entirely and leave a chip failing
//     its verify pass that the tester-driven exact path would have fully
//     repaired. tests/hardware/test_onchip_col_repair.py's gap_demo
//     scenario pins a real, hand-verified example of exactly this gap
//     (cross-checked against repair.bira.analyze() directly) rather than
//     leaving this as an unverified claim.
//   * This is NEVER a false pass, only ever a false unrepairable. Whatever
//     row_repair_en/faulty_row_addr/col_repair_en/faulty_bit this module
//     latches, onchip_selfrepair_ctrl's own verify-by-re-execution re-runs
//     the march algorithm THROUGH whatever remap state actually got
//     applied and independently re-derives bist_fail from that real
//     hardware behavior -- completely independent of this module's own
//     internal bookkeeping. A wrong or incomplete internal claim can only
//     ever manifest as self_repair_fail=1 (a true statement about that
//     verify pass), never as a false self_repair_done with a defect still
//     live.
//   * seen_once[] is a DENSE per-bit-lane bitmap (one flop per bit, no
//     capacity limit), not a bounded candidate table. A bounded table would
//     permanently fill with "noise" from first-sighted bits that never
//     recur (accumulate-for-lifetime, nothing ever evicts a non-promoted
//     entry), and once full, a GENUINE later recurrence would go
//     undetected -- silently treated as a first sighting again, an
//     avoidable extra degradation on top of the fundamentally-accepted
//     single-pass gap above. A dense bitmap has no such capacity limit for
//     the "have I seen this bit before" question at all, and is cheaper:
//     O(DATA_WIDTH) flops vs. a CAM's
//     O(DATA_WIDTH x NUM_SPARE_COLS x BIT_IDX_WIDTH) comparators.
//   * Column-slot allocation within one cycle is a SEQUENTIAL priority
//     chain (ascending bit index), not independent parallel per-bit
//     decisions: each bit's search for a free column slot sees the
//     PROVISIONAL occupancy already claimed by EARLIER (lower-index) bits
//     in the SAME cycle, not a stale pre-cycle snapshot. Two
//     simultaneously-first-recurring bits in one word computing "the first
//     free slot" from the same snapshot would otherwise both compute the
//     SAME answer and one would silently clobber the other's claim -- a
//     genuine data-loss hazard, not merely a capacity limit. This is the
//     same discipline a priority encoder/leading-zero-counter needs, not a
//     new pattern. It is computed like one, too: from a running count of the
//     lower bits that want a column, built as a tree (see the decision block),
//     not by walking the bits one after another, which was too slow for a
//     20 ns clock at sky130's slow corner on a 32-bit, 4-words-per-row memory.
//   * At NUM_SPARE_COLS==0, this degenerates to EXACTLY
//     onchip_row_repair_analyzer.sv's own behavior: rule 3's column-claim
//     attempt trivially always fails (there are never any free column
//     slots), falls back to wanting a row claim (same fallback as a
//     genuine capacity-exhaustion case), and the row-CAM logic below is
//     byte-identical to the row-only module's. Verified directly, not
//     assumed: tests/hardware/test_onchip_col_repair.py's degenerate
//     scenario configures NUM_SPARE_COLS=0 and asserts the resulting
//     repair signature matches what onchip_row_repair_analyzer.sv alone
//     would have produced for the identical fault set.
//   * repair_load (persisted-repair-signature restore) stays ROW-ONLY,
//     byte-identical contract to onchip_row_repair_analyzer.sv -- there is
//     no fuse_col_repair_en/fuse_faulty_bit input. Naively restoring a
//     row-only persisted signature into this module would silently forget
//     any previously-computed COLUMN claims on every reset. Config
//     validation rejects onchip_col_repair combined with
//     onchip_repair_persistence outright (generator.py) rather than
//     allowing that silent gap -- this module does not need to guard
//     against it itself.
//   * Same accumulate-for-the-chip's-lifetime semantics as
//     onchip_row_repair_analyzer.sv, extended to the column state too:
//     live_col_valid/live_col_bit/seen_once are cleared only by rst_n,
//     never by a re-trigger, for the identical reason (a hard defect never
//     un-happens; see that module's header for the full argument, which
//     applies unchanged here).
//   * Column muxing (WORDS_PER_ROW > 1): a row is a PHYSICAL row (the row
//     CAM compares only the row bits, as onchip_row_repair_analyzer.sv does),
//     and a column is a PHYSICAL column -- bit b of the words whose low
//     address bits are w, index b * WORDS_PER_ROW + w, which is what a spare
//     column replaces (see rtl/repair_remap_col.sv). seen_once, first_seen,
//     first_row and the claimed columns are kept per physical column, so the
//     per-bit-lane state grows WORDS_PER_ROW times; faulty_bit names physical
//     columns, as repair/bisr.py::encode_repair packs them. The rules above
//     are unchanged, read with "bit" meaning that physical column.
//   * Column muxing also adds one pipeline stage (PIPE). Looking up each
//     bit's physical column, b * WORDS_PER_ROW + w, means fanning the failing
//     address's column bits out to every bit's WORDS_PER_ROW-way select;
//     together with the claim decision in one cycle, that missed a 20 ns
//     clock at sky130's slow corner (selfrepair_mp, 4 words per row). So the
//     first stage registers every input and each bit's looked-up column
//     state; the second decides and commits, a cycle later. Every input is
//     delayed alike, so the sequencer sees the same order of events and only
//     the outputs move a cycle: the sequencer kicks the next pass two cycles
//     after latch_result (S_DECIDE between), the cycle the latched repair now
//     reaches the remaps, and the pass's first access comes later still. Two
//     things keep the decision exact: the update being committed while the
//     next lookup is made is folded into it (fwd: seen_once and first_seen
//     are only ever set, by the decision itself), and has_suspect includes a
//     verify fail still in the decision stage, since the sequencer reads it
//     in the cycle bist_done rises. At WORDS_PER_ROW == 1 there is no stage,
//     and the outputs come in the same cycle as before it existed.
//   * Spare retirement (redundancy.onchip_spare_retry), as in
//     onchip_row_repair_analyzer.sv, for both kinds of spare: during the verify
//     pass a fail at a row-remapped row blames that row's spare; a fail at any
//     other row blames the spare column of each failing bit that is
//     column-remapped (a cell where both remaps apply comes from the spare
//     row, which is blamed). A retire pulse marks the suspects bad, drops their
//     claims and remap enables, and allocation skips bad spares from then on.
module onchip_2d_repair_analyzer #(
    parameter integer ADDR_WIDTH     = 10,
    parameter integer DATA_WIDTH     = 32,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer DEFERRED       = 0,   // 1: the two-pass deferred mode (see the header)
    parameter integer WORDS_PER_ROW  = 1,   // column-mux factor, a power of two (see the header)
    parameter integer BIT_IDX_WIDTH  = (DATA_WIDTH * WORDS_PER_ROW > 1) ? $clog2(DATA_WIDTH * WORDS_PER_ROW) : 1,
    // The pipeline stage (see the header), on with column muxing. Left at its
    // default by the generated wrapper; a test sets it to compare the two.
    parameter integer PIPELINE       = (WORDS_PER_ROW > 1)
) (
    input  logic clk,
    input  logic rst_n,

    input  logic                  enable,       // registrar tracks fails only while high
    input  logic                  fail_valid,
    input  logic [ADDR_WIDTH-1:0] fail_addr,
    input  logic [DATA_WIDTH-1:0] fail_bitmask, // valid whenever fail_valid is
    input  logic                  latch_result, // pulse: publishes accumulated state to outputs

    // Row-only persistence, byte-identical contract to
    // onchip_row_repair_analyzer.sv -- see header for why this stays row-only.
    input  logic                                 repair_load,
    input  logic [NUM_SPARE_ROWS-1:0]            fuse_row_repair_en,
    input  logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0] fuse_faulty_row_addr,
    output logic                                 repair_load_done,  // sticky, cleared only by rst_n

    output logic [NUM_SPARE_ROWS-1:0]               row_repair_en,
    output logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0]    faulty_row_addr,
    output logic [NUM_SPARE_COLS-1:0]               col_repair_en,
    output logic [NUM_SPARE_COLS*BIT_IDX_WIDTH-1:0] faulty_bit,
    output logic                                    unrepairable,

    // --- Spare retirement (see the header), additive/optional ---
    input  logic                                    verify_enable, // high during the verify pass
    input  logic                                    retire,        // pulse: suspects become bad
    output logic                                    has_suspect,   // a verify fail hit a remapped spare

    // --- Deferred mode (DEFERRED = 1): high during the first of the two analyze
    // passes, from the sequencer (onchip_selfrepair_ctrl TWO_PASS); ignored at
    // DEFERRED = 0 ---
    input  logic                                    defer
);

    localparam integer COL_BITS = $clog2(WORDS_PER_ROW);
    localparam integer NUM_COLS = DATA_WIDTH * WORDS_PER_ROW;  // physical columns
    // Column muxing adds a pipeline stage (see the header): the decision below
    // then works on the inputs one cycle late, with the per-bit column state
    // looked up the cycle before.
    localparam bit PIPE = (PIPELINE != 0);

    // The inputs the decision stage works on: the ports one cycle late when
    // PIPE, the ports themselves otherwise. Every input is delayed alike, so
    // the order of fails, passes, latches and retires is unchanged.
    logic                                 en_i, fv_i, latch_i, verify_i, retire_i, defer_i, load_i;
    logic [ADDR_WIDTH-1:0]                fa_i;
    logic [DATA_WIDTH-1:0]                fb_i;
    logic [NUM_SPARE_ROWS-1:0]            fuse_en_i;
    logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0] fuse_addr_i;

    // The failing word's column bits: its bit b is physical column
    // b * WORDS_PER_ROW + fail_wcol. Always 0 without muxing.
    logic [31:0] fail_wcol;
    assign fail_wcol = fa_i % WORDS_PER_ROW;

    // ---- Row CAM: identical structure to onchip_row_repair_analyzer.sv ----
    logic                  live_row_valid [0:NUM_SPARE_ROWS-1];
    logic [ADDR_WIDTH-1:0] live_row_addr  [0:NUM_SPARE_ROWS-1];
    logic [NUM_SPARE_ROWS-1:0] bad_row;      // retired spares, for the chip's lifetime
    logic [NUM_SPARE_ROWS-1:0] suspect_row;  // spares the current verify pass found failing
    logic [NUM_SPARE_COLS-1:0] bad_col;
    logic [NUM_SPARE_COLS-1:0] suspect_col;

    logic   already_row_registered;
    logic   found_free_row;
    integer free_row;

    always_comb begin
        already_row_registered = 1'b0;
        found_free_row         = 1'b0;
        free_row               = 0;
        for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
            if (live_row_valid[i] && (live_row_addr[i] >> COL_BITS) == (fa_i >> COL_BITS)) begin
                already_row_registered = 1'b1;
            end
            if (!found_free_row && !live_row_valid[i] && !bad_row[i]) begin
                found_free_row = 1'b1;
                free_row       = i;
            end
        end
    end

    // ---- Column side: dense seen-before bitmap + a small claimed-column CAM ----
    // Per-bit signals are PACKED vectors (bit-select/part-select access), not
    // unpacked arrays indexed by a loop variable: Icarus's support for the
    // latter inside always_* processes is incomplete (confirmed directly --
    // an earlier unpacked-array version of this module failed to elaborate).
    // Part-select-with-variable-base (col_claim_slot[b*BIT_IDX_WIDTH +:
    // BIT_IDX_WIDTH]) is the SAME pattern onchip_row_repair_analyzer.sv
    // already uses for faulty_row_addr, proven to work. The small,
    // NUM_SPARE_COLS-sized CAM arrays below stay unpacked, matching that same
    // proven module's live_valid/live_addr convention -- the elaboration
    // failure was specific to DATA_WIDTH-sized unpacked arrays, not small
    // ones.
    // Indexed by physical column (the bit, without muxing).
    logic [NUM_COLS-1:0]    seen_once;
    // Deferred mode: per physical column, whether it has failed yet and at
    // which address (packed, like seen_once). Never written at DEFERRED = 0.
    logic [NUM_COLS-1:0]            first_seen;
    logic [NUM_COLS*ADDR_WIDTH-1:0] first_row;
    logic                             defer_mode;
    assign defer_mode = (DEFERRED != 0) && defer_i;

    // Per bit b, the state of physical column b * WORDS_PER_ROW + w, w the
    // failing word's column bits: seen before, first seen (deferred), and
    // first seen at a different physical row. Looked up from the fail_addr
    // PORT -- the same cycle's decision without PIPE, the next cycle's with it.
    logic [31:0]           in_wcol;
    logic [DATA_WIDTH-1:0] seen_now, first_now, rowne_now;  // looked up now
    logic [DATA_WIDTH-1:0] seen_lk, first_lk, rowne_lk;     // what the decision stage gets
    assign in_wcol = fail_addr % WORDS_PER_ROW;
    always_comb begin
        for (int b = 0; b < DATA_WIDTH; b++) begin
            seen_now[b]  = seen_once[b * WORDS_PER_ROW + in_wcol];
            first_now[b] = first_seen[b * WORDS_PER_ROW + in_wcol];
            rowne_now[b] = (first_row[(b * WORDS_PER_ROW + in_wcol) * ADDR_WIDTH +: ADDR_WIDTH] >> COL_BITS)
                           != (fail_addr >> COL_BITS);
        end
    end

    generate
        if (PIPE) begin : g_pipe
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    en_i <= 1'b0; fv_i <= 1'b0; latch_i <= 1'b0; verify_i <= 1'b0;
                    retire_i <= 1'b0; defer_i <= 1'b0; load_i <= 1'b0;
                    fa_i <= '0; fb_i <= '0; fuse_en_i <= '0; fuse_addr_i <= '0;
                    seen_lk <= '0; first_lk <= '0; rowne_lk <= '0;
                end else begin
                    en_i <= enable; fv_i <= fail_valid; latch_i <= latch_result; verify_i <= verify_enable;
                    retire_i <= retire; defer_i <= defer; load_i <= repair_load;
                    fa_i <= fail_addr; fb_i <= fail_bitmask;
                    fuse_en_i <= fuse_row_repair_en; fuse_addr_i <= fuse_faulty_row_addr;
                    seen_lk <= seen_now; first_lk <= first_now; rowne_lk <= rowne_now;
                end
            end
        end else begin : g_no_pipe
            assign en_i = enable;          assign fv_i = fail_valid;
            assign latch_i = latch_result; assign verify_i = verify_enable;
            assign retire_i = retire;      assign defer_i = defer;
            assign load_i = repair_load;
            assign fa_i = fail_addr;       assign fb_i = fail_bitmask;
            assign fuse_en_i = fuse_row_repair_en; assign fuse_addr_i = fuse_faulty_row_addr;
            assign seen_lk = seen_now; assign first_lk = first_now; assign rowne_lk = rowne_now;
        end
    endgenerate

    // PIPE: a lookup misses the update the decision stage was committing in
    // the same cycle -- the previous fail's -- so that update is kept
    // (upd_*) and folded in when it hit the same word column. seen_once and
    // first_seen are only ever set, by that stage, so nothing else is missed.
    logic                  upd_valid;
    logic [ADDR_WIDTH-1:0] upd_addr;
    logic [DATA_WIDTH-1:0] upd_seen, upd_first;
    logic                  fwd;
    logic [DATA_WIDTH-1:0] seen_eff, first_eff, rowne_eff;
    assign fwd = PIPE && upd_valid && (upd_addr % WORDS_PER_ROW) == fail_wcol;
    always_comb begin
        for (int b = 0; b < DATA_WIDTH; b++) begin
            seen_eff[b]  = seen_lk[b]  || (fwd && upd_seen[b]);
            first_eff[b] = first_lk[b] || (fwd && upd_first[b]);
            rowne_eff[b] = (fwd && upd_first[b]) ? ((upd_addr >> COL_BITS) != (fa_i >> COL_BITS)) : rowne_lk[b];
        end
    end
    logic                     live_col_valid        [0:NUM_SPARE_COLS-1];
    logic [BIT_IDX_WIDTH-1:0] live_col_bit          [0:NUM_SPARE_COLS-1];
    logic [BIT_IDX_WIDTH-1:0] commit_slot;

    // Per-bit decision. Within one word, the bits that want a column take the
    // free column slots in ascending bit order -- the j-th such bit the j-th
    // free slot, by ascending slot index -- a priority chain in effect (see
    // the header). It is computed from a running count of the earlier bits
    // that want one, built as a tree, so its depth grows with log2(DATA_WIDTH)
    // rather than DATA_WIDTH: written bit by bit, the chain missed a 20 ns
    // clock at sky130's slow corner. tests/hardware/tb_2d_analyzer_pipeline.sv
    // checks it, decision for decision, against the bit-by-bit form
    // (tests/hardware/onchip_2d_repair_analyzer_seq.sv). col_claim_valid[b] and
    // col_claim_slot's b-th slice carry each bit's claim out to the always_ff
    // below, which commits exactly these slots.
    localparam integer NCS = (NUM_SPARE_COLS > 0) ? NUM_SPARE_COLS : 1;
    localparam integer CW  = $clog2(NCS + 1);  // a count of 0..NUM_SPARE_COLS
    logic                  active;              // this word is being analyzed
    logic [DATA_WIDTH-1:0] covered;             // rule 2: a claimed column covers the bit
    logic [DATA_WIDTH-1:0] cand;                // rule 3: wants a column
    logic [DATA_WIDTH-1:0] first_sight;         // rule 4: wants a row
    logic [NCS-1:0]        col_free;            // not claimed, not retired
    logic [CW-1:0]         num_free;
    logic [NCS*CW-1:0]     free_rank;           // per free slot: free slots below it
    logic [DATA_WIDTH*CW-1:0] cand_cnt, cand_nxt;  // running counts, saturating at NUM_SPARE_COLS
    logic [DATA_WIDTH*CW-1:0] cand_before;         // per bit: candidates below it
    logic [DATA_WIDTH-1:0]               wants_row;
    logic [DATA_WIDTH-1:0]               col_claim_valid;
    logic [DATA_WIDTH*BIT_IDX_WIDTH-1:0] col_claim_slot;
    logic [DATA_WIDTH-1:0]               wants_below;  // some lower bit wants a row
    integer                              sum, rank;

    assign active = en_i && fv_i && !already_row_registered;

    always_comb begin
        num_free  = '0;
        free_rank = '0;
        rank      = 0;
        for (int k = 0; k < NCS; k++) begin
            col_free[k] = (k < NUM_SPARE_COLS) && !live_col_valid[k] && !bad_col[k];
            free_rank[k*CW +: CW] = rank;
            if (col_free[k]) rank = rank + 1;
        end
        num_free = rank;

        for (int b = 0; b < DATA_WIDTH; b++) begin
            covered[b] = 1'b0;
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (live_col_valid[k] && live_col_bit[k] == b * WORDS_PER_ROW + fail_wcol) begin
                    covered[b] = 1'b1;
                end
            end
            // rule 3 (deferred pass one: only a recurrence at a DIFFERENT row
            // counts) versus rule 4 (first sighting: row wins ties).
            cand[b]        = active && fb_i[b] && !covered[b]
                             && (defer_mode ? (first_eff[b] && rowne_eff[b]) : seen_eff[b]);
            first_sight[b] = active && fb_i[b] && !covered[b]
                             && !(defer_mode ? (first_eff[b] && rowne_eff[b]) : seen_eff[b]);
        end

        // Inclusive running count of cand, a doubling (Kogge-Stone) tree.
        for (int b = 0; b < DATA_WIDTH; b++) cand_cnt[b*CW +: CW] = cand[b];
        for (int st = 1; st < DATA_WIDTH; st = st * 2) begin
            for (int b = 0; b < DATA_WIDTH; b++) begin
                if (b >= st) begin
                    sum = cand_cnt[b*CW +: CW] + cand_cnt[(b-st)*CW +: CW];
                    cand_nxt[b*CW +: CW] = (sum > NUM_SPARE_COLS) ? NUM_SPARE_COLS : sum;
                end else begin
                    cand_nxt[b*CW +: CW] = cand_cnt[b*CW +: CW];
                end
            end
            cand_cnt = cand_nxt;
        end

        col_claim_valid = '0;
        col_claim_slot  = '0;
        for (int b = 0; b < DATA_WIDTH; b++) begin
            cand_before[b*CW +: CW] = (b == 0) ? '0 : cand_cnt[(b-1)*CW +: CW];
            // The j-th candidate takes the j-th free slot while any is left;
            // past them it falls back to wanting a row (not a straight
            // unrepairable).
            col_claim_valid[b] = cand[b] && (cand_before[b*CW +: CW] < num_free);
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (col_free[k] && free_rank[k*CW +: CW] == cand_before[b*CW +: CW]) begin
                    col_claim_slot[b*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] = k[BIT_IDX_WIDTH-1:0];
                end
            end
            wants_row[b] = first_sight[b] || (cand[b] && !col_claim_valid[b]);
        end

        // For "two or more": some lower bit also wants a row (prefix OR tree).
        wants_below = wants_row;
        for (int st = 1; st < DATA_WIDTH; st = st * 2) wants_below = wants_below | (wants_below << st);
        wants_below = wants_below << 1;
    end

    // Greedy: any bit left wanting a row asks for one. Deferred pass one: only a
    // word with two or more such bits does (a lone first sighting is
    // remembered instead, below).
    logic word_wants_row;
    logic word_takes_row;
    assign word_wants_row = active && (defer_mode ? ((wants_row & wants_below) != '0) : (|wants_row));
    assign word_takes_row = word_wants_row && found_free_row;

    // The 2D analyzer never declares a chip unrepairable itself: it is lenient,
    // and the sequencer's verify pass decides (see the header).
    assign unrepairable = 1'b0;

    // Verify pass: blame the spare that served the failing cell. A
    // row-remapped row's cells all come from its spare row; at any other row,
    // a failing column-remapped bit comes from its spare column.
    logic [NUM_SPARE_ROWS-1:0] suspect_row_set;
    logic [NUM_SPARE_COLS-1:0] suspect_col_set;
    always_comb begin
        suspect_row_set = '0;
        suspect_col_set = '0;
        if (verify_i && fv_i) begin
            if (already_row_registered) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    if (live_row_valid[i] && (live_row_addr[i] >> COL_BITS) == (fa_i >> COL_BITS)) begin
                        suspect_row_set[i] = 1'b1;
                    end
                end
            end else begin
                for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                    if (live_col_valid[k] && fb_i[live_col_bit[k] >> COL_BITS]
                        && live_col_bit[k] % WORDS_PER_ROW == fail_wcol) begin
                        suspect_col_set[k] = 1'b1;
                    end
                end
            end
        end
    end
    // The sequencer reads has_suspect in the cycle bist_done rises; with PIPE
    // the last verify fail may still be in the decision stage then, so it
    // counts here as it already would have without PIPE.
    assign has_suspect = (|suspect_row) || (|suspect_col)
                         || (PIPE && ((|suspect_row_set) || (|suspect_col_set)));

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                live_row_valid[i] <= 1'b0;
                live_row_addr[i]  <= '0;
            end
            seen_once  <= '0;
            first_seen <= '0;
            first_row  <= '0;
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                live_col_valid[k] <= 1'b0;
                live_col_bit[k]   <= '0;
            end
            row_repair_en     <= '0;
            faulty_row_addr   <= '0;
            col_repair_en     <= '0;
            faulty_bit        <= '0;
            repair_load_done  <= 1'b0;
            bad_row           <= '0;
            suspect_row       <= '0;
            bad_col           <= '0;
            suspect_col       <= '0;
            upd_valid         <= 1'b0;
            upd_addr          <= '0;
            upd_seen          <= '0;
            upd_first         <= '0;
        end else begin
            for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                if (suspect_row_set[i]) suspect_row[i] <= 1'b1;
            end
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                if (suspect_col_set[k]) suspect_col[k] <= 1'b1;
            end

            // This cycle's seen_once/first_seen sets, for the lookup the next
            // decision got while they were being committed (fwd above).
            upd_valid <= en_i && fv_i && !already_row_registered;
            upd_addr  <= fa_i;
            for (int b = 0; b < DATA_WIDTH; b++) begin
                upd_seen[b]  <= fb_i[b] && wants_row[b]
                                && (!defer_mode || (!word_takes_row && !first_eff[b]));
                upd_first[b] <= fb_i[b] && wants_row[b] && defer_mode && !word_takes_row && !first_eff[b];
            end

            // Bit bookkeeping: mark first sightings seen, commit column
            // claims decided combinationally above -- same slot index, no
            // re-computation, no race (each claimed bit writes a DIFFERENT
            // live_col_* index, guaranteed by the sequential threading above,
            // the same "dynamic-index array write in a bounded loop" pattern
            // the row CAM below already uses for live_row_valid[free_row]).
            if (en_i && fv_i && !already_row_registered) begin
                for (int b = 0; b < DATA_WIDTH; b++) begin
                    if (fb_i[b]) begin
                        if (wants_row[b] && !defer_mode) begin
                            seen_once[b * WORDS_PER_ROW + fail_wcol] <= 1'b1;
                        end
                        // Deferred pass one: a bit left over in a word that
                        // takes no row is remembered with the row it first
                        // failed at; pass two treats it as seen.
                        if (wants_row[b] && defer_mode && !word_takes_row && !first_eff[b]) begin
                            first_seen[b * WORDS_PER_ROW + fail_wcol] <= 1'b1;
                            first_row[(b * WORDS_PER_ROW + fail_wcol) * ADDR_WIDTH +: ADDR_WIDTH] <= fa_i;
                            seen_once[b * WORDS_PER_ROW + fail_wcol]  <= 1'b1;
                        end
                        if (col_claim_valid[b]) begin
                            commit_slot = col_claim_slot[b*BIT_IDX_WIDTH +: BIT_IDX_WIDTH];
                            live_col_valid[commit_slot] <= 1'b1;
                            live_col_bit[commit_slot]   <= b * WORDS_PER_ROW + fail_wcol;
                        end
                    end
                end
            end

            // Row claim: identical shape to onchip_row_repair_analyzer.sv,
            // applied ONCE per word (word_wants_row), never once per bit --
            // multiple bits wanting a row this cycle still only ever spend
            // ONE row slot on their shared address.
            // With no row left nothing is decided here: a later read of the
            // same word may still claim columns for its bits, and the verify
            // pass says whether the repair holds (see the header).
            if (word_takes_row) begin
                live_row_valid[free_row] <= 1'b1;
                live_row_addr[free_row]  <= fa_i;
            end

            // Placed after the live-fail blocks above, same last-write-wins
            // precedence and the same documented repair_load contract as
            // onchip_row_repair_analyzer.sv (see that module's port-list
            // comment for the full timing argument, which applies unchanged
            // here since persistence stays row-only).
            if (load_i) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    live_row_valid[i] <= fuse_en_i[i];
                    live_row_addr[i]  <= fuse_addr_i[i*ADDR_WIDTH +: ADDR_WIDTH];
                    row_repair_en[i]  <= fuse_en_i[i];
                    faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= fuse_addr_i[i*ADDR_WIDTH +: ADDR_WIDTH];
                end
                repair_load_done <= 1'b1;
            end

            if (latch_i) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    row_repair_en[i] <= live_row_valid[i];
                    faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= live_row_addr[i];
                end
                for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                    col_repair_en[k] <= live_col_valid[k];
                    faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] <= live_col_bit[k];
                end
                suspect_row  <= '0;  // a fresh allocation gets a fresh verify
                suspect_col  <= '0;
            end

            // Retire: only ever pulsed between passes (the sequencer's
            // S_RETIRE), never in the same cycle as a fail or a latch.
            if (retire_i) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    if (suspect_row[i]) begin
                        bad_row[i]        <= 1'b1;
                        live_row_valid[i] <= 1'b0;
                        row_repair_en[i]  <= 1'b0;
                    end
                end
                for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                    if (suspect_col[k]) begin
                        bad_col[k]        <= 1'b1;
                        live_col_valid[k] <= 1'b0;
                        col_repair_en[k]  <= 1'b0;
                    end
                end
                suspect_row <= '0;
                suspect_col <= '0;
            end
        end
    end

endmodule
