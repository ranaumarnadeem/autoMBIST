// Testbench for a SIB-network-inserted `sram_1rw_mbist` module built from the
// TESTER-DRIVEN repair_ports: config (row_repair_en/faulty_row_addr/
// col_repair_en/faulty_bit -- no onchip_selfrepair, so none of
// self_repair_*/repair_load/diag_* exist on this DUT shape at all). Unlike
// tb_sram_1rw_mbist.v (which ties the functional bus permanently idle), THIS
// testbench also drives the functional bus every cycle -- proving a
// JTAG-driven repair-port write actually steers a subsequent functional
// access, not just that the write reaches the port. test_mode/bist_start are
// still present (every wrapper has the base 4) and are tied off/never
// written, so `test_mode` stays 0 and the functional bus stays connected
// straight through to the (remapped) memory, per wrapper_template.j2's own
// `mode_signal = test_mode` (tester-driven path, no `effective_*` signals).
//
// clk and tck driven from the SAME physical clock (lockstep), mirroring
// tb_sram_1rw_mbist.v/warptap's own tb_mem_subsystem_mbist.v convention.
//
// Stimulus file: one line of eight integers each:
// "<tms> <tdi> <trst_n> <rst_n> <csb> <we> <addr> <wdata>".
// func_we is ACTIVE-HIGH at the wrapper boundary (1 = write), confirmed
// against wrapper_template.j2's own `selected_write_req = mode_signal ?
// mbist_write_req : func_we` feeding `sram_we = ~selected_write_req` --
// independent of the config's own we_active_low setting, which only affects
// the internal mux's output polarity toward the real memory, not this
// boundary port. func_csb is ACTIVE-LOW (0 = selected), matching every other
// testbench in this project.
`timescale 1ns/1ps

module tb_sram_1rw_repair_ports;
    reg clk = 0;
    reg tms = 0;
    reg tdi = 0;
    reg trst_n = 1;
    reg rst_n = 1;
    reg csb = 1;
    reg we = 0;
    reg [3:0] addr = 0;
    reg [7:0] wdata = 0;
    wire tdo;
    wire [7:0] func_dout;

    sram_1rw_mbist dut (
        .clk(clk),
        .rst_n(rst_n),
        .test_mode(1'b0),
        .bist_start(1'b0),
        .bist_done(),
        .bist_fail(),
        .func_csb(csb),
        .func_addr(addr),
        .func_din(wdata),
        .func_we(we),
        .func_dout(func_dout),
        .row_repair_en(2'b0),
        .faulty_row_addr(8'b0),
        .col_repair_en(1'b0),
        .faulty_bit(3'b0),
        .tck(clk),
        .tms(tms),
        .tdi(tdi),
        .trst_n(trst_n),
        .tdo(tdo)
    );

    integer fd;
    integer code;
    integer tms_in, tdi_in, trst_n_in, rst_n_in, csb_in, we_in, addr_in, wdata_in;
    reg done;

    initial begin
        done = 0;
        fd = $fopen("stimulus.txt", "r");
        if (fd == 0) begin
            $display("ERROR: could not open stimulus.txt");
            $finish;
        end
        while (!done) begin
            code = $fscanf(fd, "%d %d %d %d %d %d %d %d\n",
                            tms_in, tdi_in, trst_n_in, rst_n_in, csb_in, we_in, addr_in, wdata_in);
            if (code != 8) begin
                done = 1;
            end else begin
                tms = tms_in[0];
                tdi = tdi_in[0];
                trst_n = trst_n_in[0];
                rst_n = rst_n_in[0];
                csb = csb_in[0];
                we = we_in[0];
                addr = addr_in[3:0];
                wdata = wdata_in[7:0];
                #5 clk = 1;
                $display("TRACE,%0t,%b,%b", $time, tdo, func_dout);
                #5 clk = 0;
            end
        end
        $fclose(fd);
        $finish;
    end
endmodule
