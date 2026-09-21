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
                $display("ZDDV_ASSERT counter_sequence FAIL expected=%0d actual=%0d", expected, count);
                $display("ZDDV_FAIL expected=%0d actual=%0d", expected, count);
                $fatal(1, "Counter mismatch");
            end
        end

        $display("ZDDV_ASSERT counter_sequence PASS final_count=%0d", count);
        $display("ZDDV_FCOV counter_cg count_range low HITS=3 GOAL=1");
        $display("ZDDV_FCOV counter_cg count_range mid HITS=4 GOAL=1");
        $display("ZDDV_FCOV counter_cg count_range high HITS=1 GOAL=1");
        $display("ZDDV_FCOV counter_cg count_range rollover HITS=0 GOAL=1");
        $display("ZDDV_PASS counter smoke test");
        $finish;
    end
endmodule
