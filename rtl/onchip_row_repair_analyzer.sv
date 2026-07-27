`timescale 1ns/1ps
// On-chip row-repair analyzer (Step E on-chip BIRA) -- a CAM-style registrar
// that tracks distinct failing ROW addresses streamed live by the controller
// (fail_valid/fail_addr, wired up on march_c/march_raw/march_x/mats_plus's
// FSMs, and on march_1r1w's single shared read-port compare) during a BIST
// pass, and freezes a row-repair signature into registered outputs on demand.
//
// This is the hardware equivalent of the software repair.bira.analyze() +
// repair.bisr.encode_row_repair() pipeline, SCOPED TO THE DEGENERATE ROW-ONLY
// CASE: with no spare columns, analyze()'s must-repair phase alone forces
// every distinct faulty row immediately (any positive column-degree exceeds a
// budget of 0) -- there is no combinatorial ambiguity left for a backtracking
// search to resolve, so a simple "first-come, lowest-free-slot" registrar is
// provably equivalent. This module does NOT implement (and does not need to
// implement) the general 2D branch-and-bound search in repair/bira.py.
//
// Output packing matches rtl/repair_remap_row.sv EXACTLY (no new convention):
// row_repair_en[i] enables spare i; faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH]
// is the logical address spare i replaces -- the same layout
// src/autombist/repair/bisr.py::encode_row_repair already produces for the
// tester-driven flow.
//
// Deliberate, documented properties (not bugs):
//   * Single-fail-per-cycle is assumed -- no arbitration for two simultaneous
//     fail_valid sources. Sound because every algo that reaches this module is
//     either inherently serial (march-c/march-raw/march-x/mats-plus: one
//     address in flight at a time) or has exactly one compare per cycle
//     despite being multi-port (march-1r1w: both ports share the same address
//     register, only the read port ever compares); this module is gated to
//     algo membership in generator.py's _SELFREPAIR_ALGOS for exactly that
//     reason. march-2rw's genuinely concurrent dual compare is excluded from
//     that set and would need real arbitration here.
//   * A PARTIAL repair is still applied when unrepairable is asserted: whatever
//     slots filled before the spare budget ran out remain latched into
//     row_repair_en/faulty_row_addr. This is a deliberate "fail-open-partially"
//     choice -- the caller's own re-verification pass is what actually proves
//     correctness -- not an oversight.
//   * Known defects ACCUMULATE for the life of the chip -- cleared only by
//     rst_n, never by re-running a self-repair pass. This is load-bearing, not
//     a simplification: once a repair is applied, the remap is ALWAYS active,
//     so any LATER analyze pass runs the march algorithm THROUGH the
//     already-repaired memory and (correctly, from its own point of view) sees
//     no fault at the repaired row. If a fresh pass cleared prior knowledge,
//     that "nothing wrong here" observation would latch an EMPTY signature and
//     silently erase a previously-correct repair, re-exposing a real,
//     already-fixed defect. Since a hard defect never "un-happens," only a
//     genuine reset (not just a re-trigger) is the correct point to forget one.
module onchip_row_repair_analyzer #(
    parameter integer ADDR_WIDTH     = 10,
    parameter integer NUM_SPARE_ROWS = 1
) (
    input  logic clk,
    input  logic rst_n,

    input  logic                  enable,       // registrar tracks fails only while high
    input  logic                  fail_valid,
    input  logic [ADDR_WIDTH-1:0] fail_addr,
    input  logic                  latch_result, // pulse: publishes accumulated state to outputs

    output logic [NUM_SPARE_ROWS-1:0]            row_repair_en,
    output logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0] faulty_row_addr,
    output logic                                 unrepairable
);

    logic                  live_valid [0:NUM_SPARE_ROWS-1];
    logic [ADDR_WIDTH-1:0] live_addr  [0:NUM_SPARE_ROWS-1];
    logic                  live_unrepairable;

    logic   already_registered;
    logic   found_free_slot;
    integer free_slot;

    always_comb begin
        already_registered = 1'b0;
        found_free_slot    = 1'b0;
        free_slot          = 0;
        for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
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
            for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                live_valid[i] <= 1'b0;
                live_addr[i]  <= '0;
            end
            live_unrepairable <= 1'b0;
            row_repair_en     <= '0;
            faulty_row_addr   <= '0;
            unrepairable      <= 1'b0;
        end else begin
            if (enable && fail_valid && !already_registered) begin
                if (found_free_slot) begin
                    live_valid[free_slot] <= 1'b1;
                    live_addr[free_slot]  <= fail_addr;
                end else begin
                    live_unrepairable <= 1'b1;  // sticky for the chip's lifetime
                end
            end

            if (latch_result) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    row_repair_en[i] <= live_valid[i];
                    faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= live_addr[i];
                end
                unrepairable <= live_unrepairable;
            end
        end
    end

endmodule
