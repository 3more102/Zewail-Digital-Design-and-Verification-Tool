`timescale 1ns/1ps

module tb_async_fifo;
    localparam int DATA_WIDTH = 8;
    localparam int ADDR_WIDTH = 3;
    localparam int DEPTH = 1 << ADDR_WIDTH;
    localparam int PTR_WIDTH = ADDR_WIDTH + 1;

    logic wclk = 1'b0;
    logic rclk = 1'b0;
    logic wrst_n = 1'b0;
    logic rrst_n = 1'b0;

    logic w_en = 1'b0;
    logic [DATA_WIDTH-1:0] w_data = '0;
    logic w_full;

    logic r_en = 1'b0;
    logic [DATA_WIDTH-1:0] r_data;
    logic r_empty;

    logic [DATA_WIDTH-1:0] expected_q[$];
    logic [PTR_WIDTH-1:0] prev_wgray = '0;
    logic [PTR_WIDTH-1:0] prev_rgray = '0;

    integer seed;
    integer random_init;
    integer i;
    integer writes = 0;
    integer reads = 0;

    async_fifo #(
        .DATA_WIDTH(DATA_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH)
    ) dut (
        .wclk   (wclk),
        .wrst_n (wrst_n),
        .w_en   (w_en),
        .w_data (w_data),
        .w_full (w_full),
        .rclk   (rclk),
        .rrst_n (rrst_n),
        .r_en   (r_en),
        .r_data (r_data),
        .r_empty(r_empty)
    );

    always #5 wclk = ~wclk;
    always #7 rclk = ~rclk;

    function automatic bit onehot0(input logic [PTR_WIDTH-1:0] value);
        logic [PTR_WIDTH-1:0] minus_one;
        begin
            minus_one = value - 1'b1;
            onehot0 = ((value & minus_one) == '0);
        end
    endfunction

    always @(negedge wclk) begin
        if (!wrst_n) begin
            prev_wgray = '0;
        end else begin
            if (!onehot0(dut.wgray ^ prev_wgray)) begin
                $display(
                    "ZDDV_FAIL write Gray pointer changed by more than one bit: prev=%b now=%b",
                    prev_wgray,
                    dut.wgray
                );
                $fatal(1, "Write Gray pointer violation");
            end
            prev_wgray = dut.wgray;
        end
    end

    always @(negedge rclk) begin
        if (!rrst_n) begin
            prev_rgray = '0;
        end else begin
            if (!onehot0(dut.rgray ^ prev_rgray)) begin
                $display(
                    "ZDDV_FAIL read Gray pointer changed by more than one bit: prev=%b now=%b",
                    prev_rgray,
                    dut.rgray
                );
                $fatal(1, "Read Gray pointer violation");
            end
            prev_rgray = dut.rgray;
        end
    end

    task automatic fifo_push(input logic [DATA_WIDTH-1:0] data);
        begin
            while (w_full)
                @(negedge wclk);

            @(negedge wclk);
            w_data = data;
            w_en = 1'b1;
            @(posedge wclk);
            #1;
            w_en = 1'b0;

            expected_q.push_back(data);
            writes = writes + 1;
        end
    endtask

    task automatic fifo_pop;
        logic [DATA_WIDTH-1:0] expected;
        begin
            while (r_empty)
                @(negedge rclk);

            expected = expected_q.pop_front();
            @(negedge rclk);
            r_en = 1'b1;
            @(posedge rclk);
            #1;

            if (r_data !== expected) begin
                $display(
                    "ZDDV_FAIL FIFO data mismatch expected=0x%02x actual=0x%02x",
                    expected,
                    r_data
                );
                $fatal(1, "FIFO data mismatch");
            end

            r_en = 1'b0;
            reads = reads + 1;
        end
    endtask

    task automatic wait_for_full;
        integer guard;
        begin
            guard = 0;
            while (!w_full && guard < 16) begin
                @(posedge wclk);
                guard = guard + 1;
            end
            if (!w_full) begin
                $display("ZDDV_FAIL full flag did not assert");
                $fatal(1, "FIFO full flag failure");
            end
        end
    endtask

    task automatic wait_for_empty;
        integer guard;
        begin
            guard = 0;
            while (!r_empty && guard < 16) begin
                @(posedge rclk);
                guard = guard + 1;
            end
            if (!r_empty) begin
                $display("ZDDV_FAIL empty flag did not assert");
                $fatal(1, "FIFO empty flag failure");
            end
        end
    endtask

    initial begin
        $dumpfile("waveform.vcd");
        $dumpvars(0, tb_async_fifo);

        if (!$value$plusargs("ZDDV_SEED=%d", seed))
            seed = 1;
        random_init = $urandom(seed);
        $display("ZDDV_INFO async_fifo seed=%0d", seed);

        repeat (4) @(posedge wclk);
        wrst_n = 1'b1;
        repeat (4) @(posedge rclk);
        rrst_n = 1'b1;

        // Fill the FIFO exactly to capacity and verify full behavior.
        for (i = 0; i < DEPTH; i = i + 1)
            fifo_push(DATA_WIDTH'(8'h10 + i));
        wait_for_full();

        // A write while full must be blocked. The scoreboard intentionally
        // does not accept this value; any DUT overwrite will be detected later.
        @(negedge wclk);
        w_data = 8'hEE;
        w_en = 1'b1;
        @(posedge wclk);
        #1;
        w_en = 1'b0;

        // Read half the FIFO, then allow the read pointer to synchronize back.
        for (i = 0; i < DEPTH/2; i = i + 1)
            fifo_pop();
        repeat (4) @(posedge wclk);

        // Force pointer wrap-around.
        for (i = 0; i < DEPTH/2; i = i + 1)
            fifo_push(DATA_WIDTH'(8'h80 + i));

        while (expected_q.size() != 0)
            fifo_pop();
        wait_for_empty();

        // Seeded stress phase: mixed operations across unrelated clocks.
        for (i = 0; i < 64; i = i + 1) begin
            if (expected_q.size() == 0) begin
                fifo_push(DATA_WIDTH'($urandom()));
            end else if (expected_q.size() == DEPTH) begin
                fifo_pop();
            end else if (($urandom() % 100) < 58) begin
                fifo_push(DATA_WIDTH'($urandom()));
            end else begin
                fifo_pop();
            end
        end

        while (expected_q.size() != 0)
            fifo_pop();
        wait_for_empty();

        if (writes != reads) begin
            $display("ZDDV_FAIL accounting mismatch writes=%0d reads=%0d", writes, reads);
            $fatal(1, "FIFO accounting mismatch");
        end

        $display(
            "ZDDV_PASS async FIFO data/flags/Gray-pointer verification writes=%0d reads=%0d",
            writes,
            reads
        );
        $finish;
    end

endmodule
