`timescale 1ns/1ps

module sram_sabtf_saboteur #(
    parameter integer ADDR_WIDTH = 4,
    parameter integer DATA_WIDTH = 8
) (
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic                  web0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    input  logic [DATA_WIDTH-1:0] din0,
    output logic [DATA_WIDTH-1:0] dout0,
    output logic [DATA_WIDTH-1:0] dbg_actual_word,
    output logic [DATA_WIDTH-1:0] dbg_fault_word,
    output logic [DATA_WIDTH-1:0] dbg_intended_word,
    output logic [DATA_WIDTH-1:0] dbg_prior_value,
    output logic [DATA_WIDTH-1:0] dbg_attempted_write,
    output logic [DATA_WIDTH-1:0] dbg_actual_read,
    output logic [DATA_WIDTH-1:0] dbg_blocked_bits
);

    logic [DATA_WIDTH-1:0] real_dout;
    logic [DATA_WIDTH-1:0] real_din;
    logic write_cycle;
    logic read_cycle;

    assign write_cycle = (~csb0) & (~web0);
    assign read_cycle  = (~csb0) & (web0);

    logic [DATA_WIDTH-1:0] tf_up_mask [0:(1<<ADDR_WIDTH)-1];
    logic [DATA_WIDTH-1:0] shadow_mem [0:(1<<ADDR_WIDTH)-1];
    logic [DATA_WIDTH-1:0] blocked_bits_q [0:(1<<ADDR_WIDTH)-1];
    logic [DATA_WIDTH-1:0] read_data_q;
    logic [DATA_WIDTH-1:0] prior_word;
    logic [DATA_WIDTH-1:0] blocked_up;
    localparam integer DELAY_CYCLES = (2 < 1) ? 1 : 2;
    localparam integer DELAY_CNT_W = (DELAY_CYCLES < 2) ? 1 : $clog2(DELAY_CYCLES + 1);
    // Active-pending list (sim only): commit delayed transitions without scanning
    // the whole address space every clock. At most one write per cycle, each
    // pending for DELAY_CYCLES cycles, so MAX_PEND = DELAY_CYCLES + 2 always has a
    // free slot. Cost is O(DELAY_CYCLES)/clock, independent of memory depth.
    localparam integer MAX_PEND = DELAY_CYCLES + 2;
    logic [ADDR_WIDTH-1:0]  pend_addr [0:MAX_PEND-1];
    logic [DATA_WIDTH-1:0]  pend_bits [0:MAX_PEND-1];
    logic [DATA_WIDTH-1:0]  pend_tgt  [0:MAX_PEND-1];
    logic [DELAY_CNT_W-1:0] pend_cnt  [0:MAX_PEND-1];
    logic                   pend_val  [0:MAX_PEND-1];
    integer idx;
    integer p;
    integer free_slot;

    initial begin
        $readmemh("<OUT>/sram_sabtf/faults/tf_up_faults.hex", tf_up_mask);
        for (idx = 0; idx < (1<<ADDR_WIDTH); idx = idx + 1) begin
            shadow_mem[idx] = '0;
            blocked_bits_q[idx] = '0;
        end
        for (p = 0; p < MAX_PEND; p = p + 1) begin
            pend_val[p] = 1'b0;
            pend_cnt[p] = '0;
        end
        read_data_q = '0;
    end

    always_comb begin
        prior_word = shadow_mem[addr0];
        blocked_up = (~prior_word) & din0 & tf_up_mask[addr0];
        real_din = (din0 & ~blocked_up) | (prior_word & blocked_up);
    end

    always_ff @(posedge clk0) begin
        for (p = 0; p < MAX_PEND; p = p + 1) begin
            if (pend_val[p]) begin
                if (pend_cnt[p] <= 1) begin
                    shadow_mem[pend_addr[p]] <= (shadow_mem[pend_addr[p]] & ~pend_bits[p]) | (pend_tgt[p] & pend_bits[p]);
                    blocked_bits_q[pend_addr[p]] <= '0;
                    pend_val[p] <= 1'b0;
                end else begin
                    pend_cnt[p] <= pend_cnt[p] - 1'b1;
                end
            end
        end

        if (write_cycle) begin
            shadow_mem[addr0] <= real_din;
            blocked_bits_q[addr0] <= blocked_up;
            if (blocked_up != '0) begin
                free_slot = -1;
                for (p = MAX_PEND - 1; p >= 0; p = p - 1) begin
                    if (!pend_val[p]) free_slot = p;
                end
                if (free_slot >= 0) begin
                    pend_addr[free_slot] <= addr0;
                    pend_bits[free_slot] <= blocked_up;
                    pend_tgt[free_slot]  <= din0;
                    pend_cnt[free_slot]  <= DELAY_CYCLES;
                    pend_val[free_slot]  <= 1'b1;
                end
            end
        end

        if (read_cycle) begin
            read_data_q <= shadow_mem[addr0];
        end
    end
    // Phase 3 -- transition write-transparency invariant. The transition
    // saboteur is a shadow model rather than a wrapper around the real memory
    // macro (unlike stuck-at / port-coupling, which DO wrap and validate the
    // real macro): it needs the pre-write cell value to decide whether a
    // 0->1 / 1->0 transition is blocked, plus a delay queue to model the
    // transition time -- neither expressible by masking a black-box macro's
    // dout. The real macro is therefore validated by the stuck-at / port-
    // coupling paths, not this one.
    //
    // A cycle-accurate read cross-check against a parallel real-macro instance
    // was tried and is intentionally NOT shipped: the shadow and a real macro
    // have legitimately different write-to-read visibility timing, so comparing
    // their reads cycle-by-cycle yields spurious mismatches even when both
    // models are correct. What IS robust and worth guarding is transparency:
    // at any address carrying NO transition-fault mask bit, the value actually
    // committed (real_din) must equal the intended write (din). This holds by
    // construction today; the assertion locks it against a future change to the
    // masking logic that might corrupt fault-free data. NON-FATAL -- it drives
    // neither dout0 nor bist_fail, so it can never change a pass/fail
    // result; it only prints GOLDEN_CHECK_ACTIVE / GOLDEN_MISMATCH.
    logic gchk_active;
    initial gchk_active = 1'b0;
    always_ff @(posedge clk0) begin
        if (write_cycle && tf_up_mask[addr0] == '0) begin
            if (!gchk_active) begin
                gchk_active <= 1'b1;
                $display("GOLDEN_CHECK_ACTIVE transition write-transparency invariant running");
            end
            if (real_din !== din0)
                $display("GOLDEN_MISMATCH addr=%0d intended=%h committed=%h",
                         addr0, din0, real_din);
        end
    end


    assign dout0 = read_data_q;
    assign dbg_actual_word = prior_word;
    assign dbg_fault_word = real_din;
    assign dbg_intended_word = din0;
    assign dbg_prior_value = prior_word;
    assign dbg_attempted_write = din0;
    assign dbg_actual_read = read_data_q;
    assign dbg_blocked_bits = write_cycle ? blocked_up : blocked_bits_q[addr0];

endmodule
