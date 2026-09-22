`timescale 1ns/1ps

module async_fifo #(
    parameter int DATA_WIDTH = 8,
    parameter int ADDR_WIDTH = 3
) (
    input  logic                  wclk,
    input  logic                  wrst_n,
    input  logic                  w_en,
    input  logic [DATA_WIDTH-1:0] w_data,
    output logic                  w_full,

    input  logic                  rclk,
    input  logic                  rrst_n,
    input  logic                  r_en,
    output logic [DATA_WIDTH-1:0] r_data,
    output logic                  r_empty
);

    localparam int DEPTH = 1 << ADDR_WIDTH;
    localparam int PTR_WIDTH = ADDR_WIDTH + 1;

    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    logic [PTR_WIDTH-1:0] wbin,  wbin_next;
    logic [PTR_WIDTH-1:0] wgray, wgray_next;
    logic [PTR_WIDTH-1:0] rbin,  rbin_next;
    logic [PTR_WIDTH-1:0] rgray, rgray_next;

    logic [PTR_WIDTH-1:0] rgray_sync1_w, rgray_sync2_w;
    logic [PTR_WIDTH-1:0] wgray_sync1_r, wgray_sync2_r;

    logic w_full_next;
    logic r_empty_next;

    initial begin
        if (ADDR_WIDTH < 2)
            $fatal(1, "async_fifo requires ADDR_WIDTH >= 2");
    end

    always_comb begin
        wbin_next  = wbin + ((w_en && !w_full) ? 1'b1 : 1'b0);
        wgray_next = (wbin_next >> 1) ^ wbin_next;

        rbin_next  = rbin + ((r_en && !r_empty) ? 1'b1 : 1'b0);
        rgray_next = (rbin_next >> 1) ^ rbin_next;
    end

    always_comb begin
        w_full_next = (
            wgray_next
            == {
                ~rgray_sync2_w[PTR_WIDTH-1:PTR_WIDTH-2],
                rgray_sync2_w[PTR_WIDTH-3:0]
            }
        );
        r_empty_next = (rgray_next == wgray_sync2_r);
    end

    always_ff @(posedge wclk or negedge wrst_n) begin
        if (!wrst_n) begin
            wbin   <= '0;
            wgray  <= '0;
            w_full <= 1'b0;
        end else begin
            if (w_en && !w_full)
                mem[wbin[ADDR_WIDTH-1:0]] <= w_data;

            wbin   <= wbin_next;
            wgray  <= wgray_next;
            w_full <= w_full_next;
        end
    end

    always_ff @(posedge rclk or negedge rrst_n) begin
        if (!rrst_n) begin
            rbin    <= '0;
            rgray   <= '0;
            r_data  <= '0;
            r_empty <= 1'b1;
        end else begin
            if (r_en && !r_empty)
                r_data <= mem[rbin[ADDR_WIDTH-1:0]];

            rbin    <= rbin_next;
            rgray   <= rgray_next;
            r_empty <= r_empty_next;
        end
    end

    always_ff @(posedge wclk or negedge wrst_n) begin
        if (!wrst_n) begin
            rgray_sync1_w <= '0;
            rgray_sync2_w <= '0;
        end else begin
            rgray_sync1_w <= rgray;
            rgray_sync2_w <= rgray_sync1_w;
        end
    end

    always_ff @(posedge rclk or negedge rrst_n) begin
        if (!rrst_n) begin
            wgray_sync1_r <= '0;
            wgray_sync2_r <= '0;
        end else begin
            wgray_sync1_r <= wgray;
            wgray_sync2_r <= wgray_sync1_r;
        end
    end

endmodule
