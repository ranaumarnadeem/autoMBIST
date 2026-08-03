`timescale 1ns/1ps
// Self-checking probe for the SPARE-COLUMN store semantics of
// tests/hardware/sram_spares_col_tiny.v (and therefore of its reference,
// rtl/sram_model_spares.sv, which carries the identical logic).
//
// This pins the single highest-severity silent-bug risk in Workstream M.1: if
// the logical-word store were a WHOLE-WORD store (`mem[addr0] <= din0;`) rather
// than a part-select to [DATA_WIDTH-1:0], the spare lanes would be written
// straight from din0's top bits, BYPASSING spare_wen0 entirely. The model would
// then pass column-repair tests that a real OpenRAM macro -- which gates its
// spare column strictly on spare_wen0 (flow/multimem/sky130_sram_32b512w.v:78-79)
// -- would fail. Nothing else in the suite would catch that.
//
// Also proves the defect knobs can only corrupt LOGICAL bits, never a spare lane
// (a defect-corrupted spare would make a repair un-provable by construction).
//
// The DUT module name is a `define so the SAME probe can be run against both
// the clone (tests/hardware/sram_spares_col_tiny.v, the default) and the
// REFERENCE model (rtl/sram_model_spares.sv) it was copied from. That matters:
// the reference is compiled by nothing else in the suite except an
// elaborate-only check at its DEFAULT parameters (NUM_SPARE_COLS = 0), where
// the entire spare-column path is dead code. Without running this probe against
// it too, the reference's spare logic has zero behavioural coverage and can rot
// silently -- exactly the drift this file's clone-based structure invites.
//
// Run standalone:
//   iverilog -g2012 -o probe <this> tests/hardware/sram_spares_col_tiny.v
//   iverilog -g2012 -DDUT_MODULE=sram_model_spares -DDUT_NEEDS_DEFECTS \
//            -o probe <this> rtl/sram_model_spares.sv
//   vvp probe
//
// DUT_NEEDS_DEFECTS: the clone bakes the defect knobs into its own parameter
// DEFAULTS; the reference defaults them to "disabled" (DEFECT_ADDR = -1), so
// driving it needs them passed explicitly to reproduce the same scenario.
`ifndef DUT_MODULE
  `define DUT_MODULE sram_spares_col_tiny
`endif

module sram_spares_col_probe;

    localparam integer AW  = 2;
    localparam integer DW  = 4;
    localparam integer NSR = 1;
    localparam integer NSC = 1;
    localparam integer MAW = 3;          // $clog2(4 + 1)
    localparam integer MDW = DW + NSC;   // 5

    logic             clk0 = 0;
    logic             csb0 = 1;
    logic             web0 = 1;
    logic [MAW-1:0]   addr0 = '0;
    logic [MDW-1:0]   din0 = '0;
    logic [MDW-1:0]   dout0;
    logic [NSC-1:0]   spare_wen0 = '0;

    integer errors = 0;

    `DUT_MODULE #(
        .ADDR_WIDTH(AW), .DATA_WIDTH(DW),
        .NUM_SPARE_ROWS(NSR), .NUM_SPARE_COLS(NSC)
`ifdef DUT_NEEDS_DEFECTS
        // The reference model defaults its defect knobs to disabled; the clone
        // bakes this scenario into its own defaults. Pass them explicitly so
        // both DUTs run the IDENTICAL scenario.
        , .DEFECT_ADDR(1),  .DEFECT_BIT(3),  .DEFECT_SA1(1)
        , .DEFECT2_ADDR(2), .DEFECT2_BIT(3), .DEFECT2_SA1(1)
`endif
    ) dut (
        .clk0(clk0), .csb0(csb0), .web0(web0), .addr0(addr0),
        .din0(din0), .dout0(dout0), .spare_wen0(spare_wen0)
    );

    always #5 clk0 = ~clk0;

    task automatic do_write(input [MAW-1:0] a, input [MDW-1:0] d, input [NSC-1:0] sw);
        begin
            @(negedge clk0);
            csb0 = 0; web0 = 0; addr0 = a; din0 = d; spare_wen0 = sw;
            @(posedge clk0);
            @(negedge clk0);
            csb0 = 1; web0 = 1; spare_wen0 = '0;
        end
    endtask

    task automatic do_read_check(input [MAW-1:0] a, input [MDW-1:0] expected,
                                 input string label);
        begin
            @(negedge clk0);
            csb0 = 0; web0 = 1; addr0 = a;
            @(posedge clk0);          // addr0_q / csb0_q / web0_q latch here
            @(negedge clk0);
            csb0 = 1;
            @(posedge clk0);          // dout0 updates here
            @(negedge clk0);
            if (dout0 !== expected) begin
                $display("MISMATCH [%s] addr=%0d got=%b exp=%b", label, a, dout0, expected);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        repeat (4) @(negedge clk0);

        // ---- 1. Seed addr 0 with the spare lane written (spare_wen0=1) ----
        // addr 0 is defect-free (defects are at rows 1 and 2).
        do_write(3'd0, 5'b1_0101, 1'b1);
        do_read_check(3'd0, 5'b1_0101, "seed spare=1");

        // ---- 2. THE R1 PROOF: a normal write with spare_wen0=0 must change
        // ONLY the logical half. If the store were whole-word, the spare lane
        // would be clobbered to din0[4]=0 here.
        do_write(3'd0, 5'b0_1010, 1'b0);
        do_read_check(3'd0, 5'b1_1010, "spare_wen=0 must not touch the spare lane");

        // ---- 3. The converse: spare_wen0=1 DOES write the spare lane ----
        do_write(3'd0, 5'b0_1010, 1'b1);
        do_read_check(3'd0, 5'b0_1010, "spare_wen=1 writes the spare lane");

        // ---- 4. A defect corrupts the LOGICAL bit only, not the spare ----
        // addr 1 is DEFECT_ADDR with DEFECT_BIT=3, SA1 -> logical bit 3 forced high.
        do_write(3'd1, 5'b1_0000, 1'b1);
        do_read_check(3'd1, 5'b1_1000, "defect forces logical bit 3 high, spare intact");

        // ---- 5. ...and cannot force the spare lane high either ----
        do_write(3'd1, 5'b0_0000, 1'b1);
        do_read_check(3'd1, 5'b0_1000, "defect must not corrupt the spare lane");

        // ---- 6. The second defect row behaves identically (same bit) ----
        do_write(3'd2, 5'b0_0000, 1'b1);
        do_read_check(3'd2, 5'b0_1000, "second defect row, same bit");

        // ---- 7. The spare ROW (physical addr 4) is defect-free and works ----
        do_write(3'd4, 5'b1_0110, 1'b1);
        do_read_check(3'd4, 5'b1_0110, "spare row stores both halves cleanly");

        if (errors != 0) begin
            $display("PROBE FAIL %0d errors", errors);
            $fatal(1);
        end
        $display("PROBE PASS spare-column store semantics");
        $finish;
    end

endmodule
