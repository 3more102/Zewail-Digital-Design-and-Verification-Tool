`timescale 1ns/1ps

module tb_counter;
    logic clk = 1'b0;
    logic rst_n = 1'b0;
    logic [3:0] count;
    logic [3:0] expected;

    counter dut (
        .clk   (clk),
        .rst_n (rst_n),
        .count (count)
    );

    always #5 clk = ~clk;

    initial begin
        $dumpfile("waveform.vcd");
        $dumpvars(0, tb_counter);

        repeat (2) @(posedge clk);
        rst_n = 1'b1;
        expected = '0;

        repeat (8) begin
            @(negedge clk);
            expected = expected + 1'b1;
            if (count !== expected) begin
                $display("ZDDV_FAIL expected=%0d actual=%0d", expected, count);
                $fatal(1, "Counter mismatch");
            end
        end

        $display("ZDDV_PASS counter smoke test");
        $finish;
    end
endmodule
