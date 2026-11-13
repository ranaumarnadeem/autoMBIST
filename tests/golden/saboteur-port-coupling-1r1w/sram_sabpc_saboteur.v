`timescale 1ns/1ps

// 1-read-port + 1-write-port saboteur: models stuck-at / transition / port-
// coupling faults over a SINGLE shared array indexed by both ports, mirroring
// sram_model_1r1w.sv's shared mem[] architecture with a fault-mask layer on
// top. write_cycle is derived from the write port
// (csb1/web1/addr1/din1);
// read_cycle from the read port (csb0/addr0). Debug taps
// sample the read path (port 0), matching test_mbist.py's dbg_* sampling.
//
// port-coupling models an inter-port bridging defect: a write on the write
// port made while the read port is reading (any other address -- the
// controllers never read and write one address in the same cycle) stores
// the word with the fault-list bits flipped. The march_1r1w controller
// (march_1r1w_fsm.sv) reads that word in a later element and waits
// READ_LATENCY cycles before comparing, so the corruption must stay visible
// on dout until then. The real memory macro's own registered dout holds a
// write's value until the address is written again; the coupling flag has
// that SAME per-address, write-scoped lifetime (set by a write that
// coincides with a read, cleared by the next write to that address).
module sram_sabpc_saboteur #(
    parameter integer ADDR_WIDTH = 4,
    parameter integer DATA_WIDTH = 8
) (
    // Read port (read-only).
    input  logic                  clk0,
    input  logic                  csb0,
    input  logic [ADDR_WIDTH-1:0] addr0,
    output logic [DATA_WIDTH-1:0] dout0,

    // Write port (write-only).
    input  logic                  clk1,
    input  logic                  csb1,
    input  logic                  web1,
    input  logic [ADDR_WIDTH-1:0] addr1,
    input  logic [DATA_WIDTH-1:0] din1,

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

    assign write_cycle = (~csb1) & (~web1);
    assign read_cycle  = (~csb0);

    logic [DATA_WIDTH-1:0] pc_mask [0:(1<<ADDR_WIDTH)-1];
    // Per-address sticky coupling-corruption flags: set (to the fault
    // pattern) on a write cycle that coincides with a read issued on the
    // read port the SAME cycle -- a genuine concurrent access -- and cleared on
    // the NEXT write to that same address (whether or not that later write
    // is itself coincident). This mirrors blocked_bits_q's per-address,
    // write-scoped lifetime in the transition-fault branches above: the
    // memory macro's own registered dout holds the corrupted value stable
    // from the write cycle onward (same 1-cycle registered-read timing as
    // stuck-at) until the address is written again, so the flag must persist
    // exactly as long as real_dout itself reflects that write -- not just for
    // one cycle -- to line up with whenever the MBIST controller actually
    // samples it (which may be more than one cycle after the write, e.g.
    // while READ_LATENCY elapses).
    logic [DATA_WIDTH-1:0] coupling_bits_q [0:(1<<ADDR_WIDTH)-1];
    integer pc_idx;

    initial begin
        $readmemh("<OUT>/sram_sabpc/faults/pc_mask.hex", pc_mask);
        for (pc_idx = 0; pc_idx < (1<<ADDR_WIDTH); pc_idx = pc_idx + 1) begin
            coupling_bits_q[pc_idx] = '0;
        end
    end

    always_ff @(posedge clk1) begin
        if (write_cycle) begin
            coupling_bits_q[addr1] <=
                read_cycle ? pc_mask[addr1] : '0;
        end
    end

    sram_sabpc #(
        .ADDR_WIDTH(ADDR_WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) u_sram (
        .clk0(clk0),
        .csb0(csb0),
        .addr0(addr0),
        .dout0(real_dout),
        .clk1(clk1),
        .csb1(csb1),
        .web1(web1),
        .addr1(addr1),
        .din1(real_din)
    );

    assign real_din = din1;
    assign dout0 = real_dout ^ coupling_bits_q[addr0];
    assign dbg_actual_word = real_dout;
    assign dbg_fault_word = dout0;
    assign dbg_intended_word = din1;
    assign dbg_prior_value = real_dout;
    assign dbg_attempted_write = din1;
    assign dbg_actual_read = real_dout;
    assign dbg_blocked_bits = coupling_bits_q[addr0];

endmodule
