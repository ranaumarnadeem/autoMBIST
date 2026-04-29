`timescale 1ns/1ps

module algo_module_probe (
    input  logic [2:0]            phase,
    input  logic [1:0]            op_step,
    output logic                  phase_dir_up,
    output logic                  do_read,
    output logic                  do_write,
    output logic [31:0]           expected_data,
    output logic [31:0]           write_data,
    output logic                  last_step
);

`ifdef ALGO_MARCH_RAW
    march_raw_algo #(
        .DATA_WIDTH(32)
    ) u_algo (
        .phase(phase),
        .op_step(op_step),
        .phase_dir_up(phase_dir_up),
        .do_read(do_read),
        .do_write(do_write),
        .expected_data(expected_data),
        .write_data(write_data),
        .last_step(last_step)
    );
`else
    march_c_algo #(
        .DATA_WIDTH(32)
    ) u_algo (
        .phase(phase),
        .op_step(op_step),
        .phase_dir_up(phase_dir_up),
        .do_read(do_read),
        .do_write(do_write),
        .expected_data(expected_data),
        .write_data(write_data),
        .last_step(last_step)
    );
`endif

endmodule