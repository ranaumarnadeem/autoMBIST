// Testbench for a SIB-network-inserted `sram_1rw_mbist` module -- a single
// generated wrapper, deliberately NOT the shared flow/multimem
// mem_subsystem_mbist.sv fixture (that hand-written 3-macro integration file
// has no persistence/diagnosis ports of its own to test). Mirrors warptap's
// own tb_mem_subsystem_mbist.v shape: clk and tck driven from the SAME
// physical clock (lockstep). test_mode/bist_start/self_repair_start/
// repair_load/fuse_row_repair_en/fuse_faulty_row_addr are all control (WRITE)
// ports and tied to constant 0/idle -- after insertion their *new* top-level
// port bits are legal but functionally vestigial, JTAG is the only path in.
// bist_done/bist_fail/self_repair_*/repair_load_done/diag_valid/diag_addr/
// diag_overflow are status (READ) ports, left unconnected (the observe cell
// taps the live host bit directly, no testbench wiring needed). The
// functional bus is tied permanently idle in this testbench itself (never
// exercised by this DUT's own tests) -- unlike tb_mem_subsystem_mbist.v's
// stimulus file, no functional-bus columns are needed here.
//
// Stimulus file: one line of four integers each: "<tms> <tdi> <trst_n> <rst_n>".
`timescale 1ns/1ps

module tb_sram_1rw_mbist;
    reg clk = 0;
    reg tms = 0;
    reg tdi = 0;
    reg trst_n = 1;
    reg rst_n = 1;
    wire tdo;
    wire [7:0] func_dout;

    sram_1rw_mbist dut (
        .clk(clk),
        .rst_n(rst_n),
        .test_mode(1'b0),
        .bist_start(1'b0),
        .bist_done(),
        .bist_fail(),
        .func_csb(1'b1),
        .func_addr(6'b0),
        .func_din(8'b0),
        .func_we(1'b0),
        .func_dout(func_dout),
        .self_repair_start(1'b0),
        .self_repair_done(),
        .self_repair_fail(),
        .self_repair_busy(),
        .repair_load(1'b0),
        .fuse_row_repair_en(2'b0),
        .fuse_faulty_row_addr(12'b0),
        .repair_load_done(),
        .diag_valid(),
        .diag_addr(),
        .diag_overflow(),
        .tck(clk),
        .tms(tms),
        .tdi(tdi),
        .trst_n(trst_n),
        .tdo(tdo)
    );

    integer fd;
    integer code;
    integer tms_in, tdi_in, trst_n_in, rst_n_in;
    reg done;

    initial begin
        done = 0;
        fd = $fopen("stimulus.txt", "r");
        if (fd == 0) begin
            $display("ERROR: could not open stimulus.txt");
            $finish;
        end
        while (!done) begin
            code = $fscanf(fd, "%d %d %d %d\n", tms_in, tdi_in, trst_n_in, rst_n_in);
            if (code != 4) begin
                done = 1;
            end else begin
                tms = tms_in[0];
                tdi = tdi_in[0];
                trst_n = trst_n_in[0];
                rst_n = rst_n_in[0];
                #5 clk = 1;
                $display("TRACE,%0t,%b", $time, tdo);
                #5 clk = 0;
            end
        end
        $fclose(fd);
        $finish;
    end
endmodule
