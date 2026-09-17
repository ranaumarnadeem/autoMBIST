`timescale 1ns/1ps
// On-chip diagnosis log -- a CAM-style registrar, structurally parallel to
// rtl/onchip_row_repair_analyzer.sv (same free-slot-allocation + dedup
// pattern) but purpose-built for DIAGNOSIS, not repair: it answers "which
// distinct addresses failed on the most recent analyze pass," a question
// onchip_row_repair_analyzer.sv cannot answer once NUM_SPARE_ROWS distinct
// fails are seen (deliberately bounded to the physical spare budget --
// correct for ITS job, useless for a full diagnostic picture).
// NUM_DIAGNOSIS_ENTRIES is an independent parameter, typically sized larger
// than NUM_SPARE_ROWS so this module can see past the repair budget.
//
// Wired to the SAME enable/fail_valid/fail_addr/latch_result signals
// onchip_row_repair_analyzer consumes -- a second, independent consumer of
// an already-existing stream, needing no onchip_selfrepair_ctrl.sv changes.
//
// Deliberate, documented properties (not bugs):
//   * Unlike onchip_row_repair_analyzer's "accumulate for the chip's
//     lifetime" semantics (load-bearing THERE because a repaired row must
//     stay known forever, or a later pass would misread "no longer failing
//     because it's repaired" as "never was broken") -- this module clears
//     its INTERNAL accumulator (live_valid/live_addr/live_overflow) at the
//     START of every analyze pass, detected as enable's rising edge, so
//     every latched snapshot reflects ONLY the most recently completed
//     pass. Diagnosis is a per-pass snapshot, not a repair record -- there
//     is no "must never forget" argument here.
//   * The registered diag_valid/diag_addr/diag_overflow outputs are touched
//     ONLY by latch_result -- exactly like row_repair_en/faulty_row_addr in
//     onchip_row_repair_analyzer. The new-pass clear above deliberately
//     touches ONLY the internal accumulator, never the outputs directly, so
//     they keep showing the PREVIOUS pass's complete result for the entire
//     duration of a new analyze pass and flip atomically to the new result
//     only when latch_result fires. Never a spurious all-zero blip on the
//     boundary pins while a pass is mid-flight.
//   * Same single-fail-per-cycle assumption as onchip_row_repair_analyzer,
//     for the same reason (gated to the same _SELFREPAIR_ALGOS membership
//     in generator.py): no arbitration for two simultaneous fail_valid
//     sources.
//   * Contract, not hardware-enforced: the accumulator's new-pass clear and
//     a live-fail registration are both derived from `enable` and could, in
//     principle, target the same cycle if fail_valid were ever asserted on
//     enable's very first cycle. Textual order below (clear first, live-
//     fail registration second) makes a same-cycle fail win its own slot
//     rather than being wiped -- the correct outcome, since a real fail on
//     that cycle must not be silently dropped. UNREACHABLE for every algo
//     gated into generator.py's _SELFREPAIR_ALGOS today:
//     onchip_selfrepair_ctrl.sv's own header proves fail_valid cannot
//     assert before S_ANALYZE_WAIT begins, one cycle after enable's rising
//     edge in S_ANALYZE_KICK. Not independently interlocked in this module,
//     the same category of caveat as onchip_row_repair_analyzer's
//     repair_load hazards -- see that module's header.
module onchip_diagnosis_log #(
    parameter integer ADDR_WIDTH            = 10,
    parameter integer NUM_DIAGNOSIS_ENTRIES = 8
) (
    input  logic clk,
    input  logic rst_n,

    input  logic                  enable,       // = registrar_enable; rising edge = new analyze pass
    input  logic                  fail_valid,
    input  logic [ADDR_WIDTH-1:0] fail_addr,
    input  logic                  latch_result, // pulse: publishes THIS pass's accumulated state

    output logic [NUM_DIAGNOSIS_ENTRIES-1:0]            diag_valid,
    output logic [NUM_DIAGNOSIS_ENTRIES*ADDR_WIDTH-1:0] diag_addr,
    output logic                                        diag_overflow
);

    logic                  live_valid [0:NUM_DIAGNOSIS_ENTRIES-1];
    logic [ADDR_WIDTH-1:0] live_addr  [0:NUM_DIAGNOSIS_ENTRIES-1];
    logic                  live_overflow;

    logic   already_registered;
    logic   found_free_slot;
    integer free_slot;

    logic   enable_q;
    logic   new_pass_start;

    assign new_pass_start = enable && !enable_q;

    always_comb begin
        already_registered = 1'b0;
        found_free_slot    = 1'b0;
        free_slot          = 0;
        for (int i = 0; i < NUM_DIAGNOSIS_ENTRIES; i++) begin
            if (live_valid[i] && live_addr[i] == fail_addr) begin
                already_registered = 1'b1;
            end
            if (!found_free_slot && !live_valid[i]) begin
                found_free_slot = 1'b1;
                free_slot       = i;
            end
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            enable_q <= 1'b0;
            for (int i = 0; i < NUM_DIAGNOSIS_ENTRIES; i++) begin
                live_valid[i] <= 1'b0;
                live_addr[i]  <= '0;
            end
            live_overflow <= 1'b0;
            diag_valid    <= '0;
            diag_addr     <= '0;
            diag_overflow <= 1'b0;
        end else begin
            enable_q <= enable;

            // Textually FIRST: see header -- a same-cycle fail (unreachable
            // today) wins its own slot rather than being cleared.
            if (new_pass_start) begin
                for (int i = 0; i < NUM_DIAGNOSIS_ENTRIES; i++) begin
                    live_valid[i] <= 1'b0;
                    live_addr[i]  <= '0;
                end
                live_overflow <= 1'b0;
            end

            if (enable && fail_valid && !already_registered) begin
                if (found_free_slot) begin
                    live_valid[free_slot] <= 1'b1;
                    live_addr[free_slot]  <= fail_addr;
                end else begin
                    live_overflow <= 1'b1;
                end
            end

            if (latch_result) begin
                for (int i = 0; i < NUM_DIAGNOSIS_ENTRIES; i++) begin
                    diag_valid[i] <= live_valid[i];
                    diag_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= live_addr[i];
                end
                diag_overflow <= live_overflow;
            end
        end
    end

endmodule
