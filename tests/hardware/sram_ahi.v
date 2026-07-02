`timescale 1ns/1ps
// A SRAM with an ACTIVE-HIGH write-enable convention (write happens when
// we0=1, not the active-low we0=0 convention every other memory model in
// this repo uses) -- exercises the wrapper template's we_active_low=false
// branch (assign sram_we = selected_write_req, no inversion), which had no
// end-to-end coverage before this memory was added. Also a non-default,
// non-power-of-two-friendly data width (16) and address width (6, depth=64).

module sram_ahi #(
    parameter integer ADDR_WIDTH = 6,
    parameter integer DATA_WIDTH = 16
) (
    input  wire                  clk0,
    input  wire                  csb0,
    input  wire [ADDR_WIDTH-1:0] addr0,
    input  wire [DATA_WIDTH-1:0] din0,
    input  wire                  we0,
    output reg [DATA_WIDTH-1:0]  dout0
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);

    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    reg                  csb0_q;
    reg                  we0_q;
    reg [ADDR_WIDTH-1:0] addr0_q;

    always @(posedge clk0) begin
        csb0_q  <= csb0;
        we0_q   <= we0;
        addr0_q <= addr0;

        if (!csb0 && we0) begin
            mem[addr0] <= din0;
        end

        if (!csb0_q && !we0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

endmodule
