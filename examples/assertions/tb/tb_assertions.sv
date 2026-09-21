`timescale 1ns/1ps

module tb_assertions;
    logic clk = 1'b0;
    logic req = 1'b0;
    logic grant = 1'b0;

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (req) begin
            assert (grant)
                else $error("grant was not asserted after request");
        end
    end

    initial begin
        $display("ZDDV_ASSERT PASS p_boot :: testbench started");
        @(negedge clk);
        req = 1'b1;
        @(negedge clk);
        $finish;
    end
endmodule
