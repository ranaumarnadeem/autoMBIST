`timescale 1ns/1ps

module mbist_tb;

    localparam integer ADDR_WIDTH = 4;
    localparam integer DATA_WIDTH = 8;

    logic clk;
    logic rst_n;
    logic bist_start;

    logic bist_busy;
    logic bist_done;
    logic bist_fail;

    logic                  sram_clk0;
    logic                  sram_csb0;
    logic                  sram_web0;
    logic [ADDR_WIDTH-1:0] sram_addr0;
    logic [DATA_WIDTH-1:0] sram_din0;
    logic [DATA_WIDTH-1:0] sram_dout0;

    mbist_top #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .READ_LATENCY(1)
    ) u_dut (
        .clk(clk),
        .rst_n(rst_n),
        .bist_start(bist_start),
        .bist_busy(bist_busy),
        .bist_done(bist_done),
        .bist_fail(bist_fail),
        .sram_clk0(sram_clk0),
        .sram_csb0(sram_csb0),
        .sram_web0(sram_web0),
        .sram_addr0(sram_addr0),
        .sram_din0(sram_din0),
        .sram_dout0(sram_dout0)
    );

    sram_model #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_mem (
        .clk0(sram_clk0),
        .csb0(sram_csb0),
        .web0(sram_web0),
        .addr0(sram_addr0),
        .din0(sram_din0),
        .dout0(sram_dout0)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        bist_start = 1'b0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        bist_start = 1'b1;
        @(posedge clk);
        bist_start = 1'b0;

        repeat (5000) begin
            @(posedge clk);
            if (bist_done) begin
                if (bist_fail) begin
                    $fatal(1, "MBIST run finished with FAIL");
                end else begin
                    $display("MBIST run finished with PASS");
                    $finish;
                end
            end
        end

        $fatal(1, "Timeout waiting for bist_done");
    end

endmodule
