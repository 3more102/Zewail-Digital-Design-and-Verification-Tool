`timescale 1ns/1ps

module tb_assertion_failure;
    initial begin
        #1;
        ASSERT_SAMPLE: assert (1'b0)
            else $error("ZDDV_ASSERT_SAMPLE expected=1 actual=0");
        #1;
        $finish;
    end
endmodule
