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
// Output packing: row_repair_en/faulty_row_addr match
// rtl/repair_remap_row.sv exactly (same convention as
// onchip_row_repair_analyzer.sv). col_repair_en/faulty_bit match
// rtl/repair_remap_col.sv exactly -- faulty_bit is a GLOBAL logical
// bit-lane index (0..DATA_WIDTH-1), the same convention
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
//     can exhaust the row+column budget entirely and report a chip
//     UNREPAIRABLE that the tester-driven exact path would have fully
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
//     new pattern.
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
module onchip_2d_repair_analyzer #(
    parameter integer ADDR_WIDTH     = 10,
    parameter integer DATA_WIDTH     = 32,
    parameter integer NUM_SPARE_ROWS = 1,
    parameter integer NUM_SPARE_COLS = 1,
    parameter integer BIT_IDX_WIDTH  = (DATA_WIDTH > 1) ? $clog2(DATA_WIDTH) : 1
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
    output logic                                    unrepairable
);

    // ---- Row CAM: identical structure to onchip_row_repair_analyzer.sv ----
    logic                  live_row_valid [0:NUM_SPARE_ROWS-1];
    logic [ADDR_WIDTH-1:0] live_row_addr  [0:NUM_SPARE_ROWS-1];
    logic                  live_unrepairable;

    logic   already_row_registered;
    logic   found_free_row;
    integer free_row;

    always_comb begin
        already_row_registered = 1'b0;
        found_free_row         = 1'b0;
        free_row               = 0;
        for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
            if (live_row_valid[i] && live_row_addr[i] == fail_addr) begin
                already_row_registered = 1'b1;
            end
            if (!found_free_row && !live_row_valid[i]) begin
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
    logic [DATA_WIDTH-1:0]    seen_once;
    logic                     live_col_valid        [0:NUM_SPARE_COLS-1];
    logic [BIT_IDX_WIDTH-1:0] live_col_bit          [0:NUM_SPARE_COLS-1];
    logic                     col_provisional_valid [0:NUM_SPARE_COLS-1];
    logic                     already_col_covered;
    logic                     claimed;
    logic [BIT_IDX_WIDTH-1:0] commit_slot;

    // Per-bit decision, sequential priority chain (NOT independent parallel
    // lanes -- see header). col_claim_valid[b]/col_claim_slot's b-th slice
    // carry each bit's column-slot decision (if any) out to the always_ff
    // below, using the SAME slot index computed here, so the register write
    // can't race against a second computation.
    logic [DATA_WIDTH-1:0]               wants_row;
    logic [DATA_WIDTH-1:0]               col_claim_valid;
    logic [DATA_WIDTH*BIT_IDX_WIDTH-1:0] col_claim_slot;

    always_comb begin
        for (int k = 0; k < NUM_SPARE_COLS; k++) begin
            col_provisional_valid[k] = live_col_valid[k];
        end

        wants_row       = '0;
        col_claim_valid = '0;
        col_claim_slot  = '0;

        for (int b = 0; b < DATA_WIDTH; b++) begin
            if (enable && fail_valid && !already_row_registered && fail_bitmask[b]) begin
                already_col_covered = 1'b0;
                for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                    if (live_col_valid[k] && live_col_bit[k] == b[BIT_IDX_WIDTH-1:0]) begin
                        already_col_covered = 1'b1;
                    end
                end

                if (already_col_covered) begin
                    // rule 2: already covered, no action.
                end else if (seen_once[b]) begin
                    // rule 3: recurrence -- try to claim a column now, against
                    // THIS-cycle provisional occupancy (earlier bits' claims
                    // already reserved above), not a stale snapshot.
                    claimed = 1'b0;
                    for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                        if (!claimed && !col_provisional_valid[k]) begin
                            col_provisional_valid[k] = 1'b1;
                            col_claim_valid[b]       = 1'b1;
                            col_claim_slot[b*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] = k[BIT_IDX_WIDTH-1:0];
                            claimed                  = 1'b1;
                        end
                    end
                    if (!claimed) begin
                        wants_row[b] = 1'b1;  // fallback, not a straight unrepairable
                    end
                end else begin
                    // rule 4: first sighting -- row wins ties.
                    wants_row[b] = 1'b1;
                end
            end
        end
    end

    logic word_wants_row;
    assign word_wants_row = enable && fail_valid && !already_row_registered && (|wants_row);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                live_row_valid[i] <= 1'b0;
                live_row_addr[i]  <= '0;
            end
            seen_once <= '0;
            for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                live_col_valid[k] <= 1'b0;
                live_col_bit[k]   <= '0;
            end
            live_unrepairable <= 1'b0;
            row_repair_en     <= '0;
            faulty_row_addr   <= '0;
            col_repair_en     <= '0;
            faulty_bit        <= '0;
            unrepairable      <= 1'b0;
            repair_load_done  <= 1'b0;
        end else begin
            // Bit bookkeeping: mark first sightings seen, commit column
            // claims decided combinationally above -- same slot index, no
            // re-computation, no race (each claimed bit writes a DIFFERENT
            // live_col_* index, guaranteed by the sequential threading above,
            // the same "dynamic-index array write in a bounded loop" pattern
            // the row CAM below already uses for live_row_valid[free_row]).
            if (enable && fail_valid && !already_row_registered) begin
                for (int b = 0; b < DATA_WIDTH; b++) begin
                    if (fail_bitmask[b]) begin
                        if (wants_row[b]) begin
                            seen_once[b] <= 1'b1;
                        end
                        if (col_claim_valid[b]) begin
                            commit_slot = col_claim_slot[b*BIT_IDX_WIDTH +: BIT_IDX_WIDTH];
                            live_col_valid[commit_slot] <= 1'b1;
                            live_col_bit[commit_slot]   <= b[BIT_IDX_WIDTH-1:0];
                        end
                    end
                end
            end

            // Row claim: identical shape to onchip_row_repair_analyzer.sv,
            // applied ONCE per word (word_wants_row), never once per bit --
            // multiple bits wanting a row this cycle still only ever spend
            // ONE row slot on their shared address.
            if (word_wants_row) begin
                if (found_free_row) begin
                    live_row_valid[free_row] <= 1'b1;
                    live_row_addr[free_row]  <= fail_addr;
                end else begin
                    live_unrepairable <= 1'b1;  // sticky for the chip's lifetime
                end
            end

            // Placed after the live-fail blocks above, same last-write-wins
            // precedence and the same documented repair_load contract as
            // onchip_row_repair_analyzer.sv (see that module's port-list
            // comment for the full timing argument, which applies unchanged
            // here since persistence stays row-only).
            if (repair_load) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    live_row_valid[i] <= fuse_row_repair_en[i];
                    live_row_addr[i]  <= fuse_faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH];
                    row_repair_en[i]  <= fuse_row_repair_en[i];
                    faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= fuse_faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH];
                end
                repair_load_done <= 1'b1;
            end

            if (latch_result) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    row_repair_en[i] <= live_row_valid[i];
                    faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= live_row_addr[i];
                end
                for (int k = 0; k < NUM_SPARE_COLS; k++) begin
                    col_repair_en[k] <= live_col_valid[k];
                    faulty_bit[k*BIT_IDX_WIDTH +: BIT_IDX_WIDTH] <= live_col_bit[k];
                end
                unrepairable <= live_unrepairable;
            end
        end
    end

endmodule
