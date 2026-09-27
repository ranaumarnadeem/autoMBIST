`timescale 1ns/1ps
// On-chip self-repair sequencer (Step E) -- runs a full inject-detect-analyze-
// apply-verify loop autonomously, with no tester: one `self_repair_start`
// level, and the chip drives its own march-C controller through an analysis
// pass, decides whether the failing rows fit the spare budget (via
// onchip_row_repair_analyzer), and (if so) re-runs the SAME march-C controller
// through the now-repaired remap to independently confirm it before reporting
// success.
//
// Every timing rule below is load-bearing -- each was either confirmed wrong
// in an early draft (and fixed) or confirmed correct only by tracing the exact
// existing march_c_fsm.sv/wrapper_template.j2 RTL, not assumed:
//
//   * ctrl_test_mode_override is a LEVEL (`ctrl_state != S_IDLE`), not a pulse.
//     The wrapper's sram_addr/din/we/csb mux is a continuously-live
//     combinational mux keyed purely on test_mode, every cycle, with no "busy"
//     qualifier. Pulsing this only at *_KICK would let the mux revert to
//     functional-port inputs the very next cycle while the algo FSM is still
//     mid-march -- mem_rdata would reflect garbage/noise, and the ST_CHECK
//     compare would fire spurious fail_valid pulses at essentially arbitrary
//     addresses, silently corrupting the whole analysis with no compile error.
//   * self_repair_done/self_repair_fail are STICKY: set once when a sequence
//     concludes (S_DECIDE's unrepairable branch, or S_VERIFY_WAIT's bist_done
//     branch), cleared only by the next self_repair_start (at S_ANALYZE_KICK)
//     or reset. A Mealy decode of S_DONE would revert to meaningless defaults
//     the instant the caller drops self_repair_start (which it must, to let
//     this FSM return to S_IDLE) -- wrong for an autonomous chip where a later
//     boot stage may query the outcome long after the sequence finished. This
//     also fixes self_repair_start's drive contract unambiguously: a LEVEL,
//     held until self_repair_done reads back high (mirrors how bist_done
//     itself is polled).
//   * bist_fail is march_c_fsm's OWN sticky fail_q, cleared unconditionally
//     every cycle the algo FSM sits in ST_IDLE -- and ST_DONE falls straight
//     back to ST_IDLE the cycle *after* `start` first reads low. Since
//     ctrl_bist_start is a clean one-cycle pulse, `start` (from the algo FSM's
//     own perspective) is already low well before bist_done first asserts, so
//     bist_fail is only valid for the SAME single cycle bist_done first reads
//     high -- one cycle later it has already been cleared back to 0 by the
//     algo FSM's own IDLE-reentry. S_VERIFY_WAIT therefore samples bist_fail
//     INTO self_repair_fail on that exact transitioning edge, never later.
//     (The existing tester-driven tests already rely on this same window --
//     _wait_bist_done returns the instant bist_done reads 1, and the caller
//     reads bist_fail immediately after, not on some later cycle.)
//   * S_DECIDE is a real, separate state, not folded into S_ANALYZE_LATCH:
//     `unrepairable` is a REGISTERED output of the analyzer, valid only the
//     cycle after latch_result pulses -- reading it combinationally during
//     S_ANALYZE_LATCH itself would see the stale pre-latch value. S_DECIDE
//     does double duty: it also guarantees the algo FSM has had a full idle
//     cycle (plus S_ANALYZE_LATCH's own cycle) to settle in ST_IDLE before
//     S_VERIFY_KICK's ctrl_bist_start pulse, since march_c_fsm's
//     ST_DONE->ST_IDLE transition needs `!start` already observed. Collapsing
//     these two states to "fix" the staleness a different way could silently
//     reintroduce a verify-kickoff hang.
//   * Tester/autonomous arbitration: S_IDLE only exits to S_ANALYZE_KICK when
//     `self_repair_start && !mbist_busy && !bist_done` (won't hijack an
//     in-progress tester-driven run, and won't mistake one for complete); the
//     wrapper additionally composes the tester's own bist_start with
//     `(ctrl_state == S_IDLE)` before it reaches the algo FSM, so a tester's
//     bist_start is ignored while this sequencer owns the algo FSM rather than
//     corrupting either flow (a bist_start pulse arriving while the algo FSM
//     is mid-run is silently dropped by march_c_fsm's own ST_IDLE-only start
//     check, and this sequencer would otherwise end up waiting on a bist_done
//     that belongs to a run it didn't start). The `!bist_done` term exists for
//     a narrower race the `!mbist_busy` term alone does not cover: if a tester
//     HOLDS its own bist_start high straight through its run's completion (not
//     dropping it before the caller also raises self_repair_start), the algo
//     FSM is parked in a STALE ST_DONE -- march_c_fsm's ST_DONE->ST_IDLE
//     transition needs `!start` observed first, so a start line that never
//     drops never edges, and mbist_busy already reads 0 in ST_DONE. Without
//     `!bist_done`, S_IDLE would exit anyway; S_ANALYZE_KICK's own
//     ctrl_bist_start pulse would then bridge seamlessly on top of the
//     tester's still-held contribution (no genuine low-then-high edge reaches
//     the algo FSM), so the "analyze" pass never actually runs a single march
//     cycle, and S_ANALYZE_WAIT reads the stale bist_done as if a fresh pass
//     had just completed -- reporting a result for a march that never
//     happened. Blocking on `!bist_done` instead makes self-repair correctly
//     WAIT for the tester to genuinely relinquish bist_start (which drops
//     bist_done for one real cycle en route back to ST_IDLE) before starting
//     its own genuine run, rather than trusting a result it never produced.
//   * registrar_enable is asserted from S_ANALYZE_KICK (not S_ANALYZE_WAIT) as
//     defensive margin: march-c's phase 0 is a pure write so fail_valid
//     provably cannot assert before S_ANALYZE_WAIT begins either way, but this
//     doesn't rely on that property holding for a future march variant.
//   * There is deliberately NO "start a fresh analysis" pulse to the analyzer
//     (no analyze_start port here) -- onchip_row_repair_analyzer accumulates
//     known defects for the chip's lifetime, cleared only by rst_n. See its
//     own header comment: since the remap is always active, a re-triggered
//     pass runs through an already-repaired memory and (correctly) sees
//     nothing wrong at a previously-repaired row; clearing prior knowledge on
//     every re-trigger would let that "nothing wrong" observation erase a
//     still-valid repair. self_repair_done/self_repair_fail (THIS module's own
//     per-run outputs) are still freshly cleared at S_ANALYZE_KICK below --
//     only the analyzer's cross-run defect knowledge persists.
module onchip_selfrepair_ctrl (
    input  logic clk,
    input  logic rst_n,

    input  logic self_repair_start,   // LEVEL: hold until self_repair_done reads back
    output logic self_repair_done,    // sticky
    output logic self_repair_fail,    // sticky
    output logic self_repair_busy,    // level, = (ctrl_state != S_IDLE)

    // Composed additively with the wrapper's existing external test_mode/
    // bist_start signals -- this module never talks to the memory bus itself.
    output logic ctrl_test_mode_override,
    output logic ctrl_bist_start,
    input  logic mbist_busy,
    input  logic bist_done,
    input  logic bist_fail,

    // To/from onchip_row_repair_analyzer.
    output logic registrar_enable,
    output logic latch_result,
    input  logic unrepairable
);

    typedef enum logic [3:0] {
        S_IDLE          = 4'd0,
        S_ANALYZE_KICK  = 4'd1,
        S_ANALYZE_WAIT  = 4'd2,
        S_ANALYZE_LATCH = 4'd3,
        S_DECIDE        = 4'd4,
        S_VERIFY_KICK   = 4'd5,
        S_VERIFY_WAIT   = 4'd6,
        S_DONE          = 4'd7
    } ctrl_state_t;

    ctrl_state_t ctrl_state;

    assign self_repair_busy        = (ctrl_state != S_IDLE);
    assign ctrl_test_mode_override = (ctrl_state != S_IDLE);
    assign ctrl_bist_start         = (ctrl_state == S_ANALYZE_KICK) || (ctrl_state == S_VERIFY_KICK);
    assign registrar_enable        = (ctrl_state == S_ANALYZE_KICK) || (ctrl_state == S_ANALYZE_WAIT);
    assign latch_result            = (ctrl_state == S_ANALYZE_LATCH);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ctrl_state       <= S_IDLE;
            self_repair_done <= 1'b0;
            self_repair_fail <= 1'b0;
        end else begin
            case (ctrl_state)
                S_IDLE: begin
                    if (self_repair_start && !mbist_busy && !bist_done) begin
                        ctrl_state <= S_ANALYZE_KICK;
                    end
                end

                S_ANALYZE_KICK: begin
                    // Cleared here, not at S_IDLE: holds the PREVIOUS run's
                    // result visible for as long as the caller keeps
                    // self_repair_start low-then-high between runs.
                    self_repair_done <= 1'b0;
                    self_repair_fail <= 1'b0;
                    ctrl_state       <= S_ANALYZE_WAIT;
                end

                S_ANALYZE_WAIT: begin
                    if (bist_done) begin
                        ctrl_state <= S_ANALYZE_LATCH;
                    end
                end

                S_ANALYZE_LATCH: begin
                    ctrl_state <= S_DECIDE;
                end

                S_DECIDE: begin
                    if (unrepairable) begin
                        // Skip the verify run entirely -- a partial repair (if
                        // any slots filled before the budget ran out) is
                        // already latched into the remap by the analyzer
                        // regardless; the caller's own independent scan is
                        // what characterizes that, not an on-chip verify pass.
                        self_repair_done <= 1'b1;
                        self_repair_fail <= 1'b1;
                        ctrl_state       <= S_DONE;
                    end else begin
                        ctrl_state <= S_VERIFY_KICK;
                    end
                end

                S_VERIFY_KICK: begin
                    ctrl_state <= S_VERIFY_WAIT;
                end

                S_VERIFY_WAIT: begin
                    if (bist_done) begin
                        // MUST sample bist_fail on this exact edge -- see the
                        // module header comment on bist_fail's validity window.
                        self_repair_done <= 1'b1;
                        self_repair_fail <= bist_fail;
                        ctrl_state       <= S_DONE;
                    end
                end

                S_DONE: begin
                    if (!self_repair_start) begin
                        ctrl_state <= S_IDLE;
                    end
                end

                default: begin
                    ctrl_state <= S_IDLE;
                end
            endcase
        end
    end

endmodule
