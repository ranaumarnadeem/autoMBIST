`timescale 1ns/1ps

// sram_2rw_dut.v with one defect in PORT 1's READ PATH only: bit 0 read
// through dout1 at address 5 is stuck at 0 (the stored array is intact, so
// port 0 reads that cell correctly). march-2rw only reads port 1 in its E2
// element (expecting all ones), so only port 1's own compare -- the packed
// [1] element of march_2rw_fsm's expected/do_read arrays -- can catch it.
// memory_name: sram_2rw_dut_stuck_bit.
module sram_2rw_dut_stuck_bit #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer DATA_WIDTH = 32
) (
    // Port 0: full read/write.
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic                  web0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    input  logic [DATA_WIDTH-1:0] din0,
    output logic [DATA_WIDTH-1:0] dout0,

    // Port 1: full read/write.
    input  logic                  clk1,
    input  logic                  csb1,
    input  logic                  web1,
    input  logic [ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0] din1,
    output logic [DATA_WIDTH-1:0] dout1
);

    localparam integer DEPTH = (1 << ADDR_WIDTH);
    localparam integer DEFECT_ADDR = 5;

    // Exactly ONE shared storage array indexed by both ports.
    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    logic                  csb0_q;
    logic                  web0_q;
    logic [ADDR_WIDTH-1:0] addr0_q;

    logic                  csb1_q;
    logic                  web1_q;
    logic [ADDR_WIDTH-1:0] addr1_q;

    always_ff @(posedge clk0) begin
        csb0_q  <= csb0;
        web0_q  <= web0;
        addr0_q <= addr0;

        if (!csb0 && !web0) begin
            mem[addr0] <= din0;
        end

        if (!csb0_q && web0_q) begin
            dout0 <= mem[addr0_q];
        end
    end

    always_ff @(posedge clk1) begin
        csb1_q  <= csb1;
        web1_q  <= web1;
        addr1_q <= addr1;

        if (!csb1 && !web1) begin
            mem[addr1] <= din1;
        end

        if (!csb1_q && web1_q) begin
            dout1 <= (addr1_q == DEFECT_ADDR) ? (mem[addr1_q] & {{(DATA_WIDTH-1){1'b1}}, 1'b0})
                                              : mem[addr1_q];
        end
    end

endmodule
