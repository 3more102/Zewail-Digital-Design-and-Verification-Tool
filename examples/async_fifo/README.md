# Asynchronous FIFO Verification Example

This example is a dual-clock asynchronous FIFO used to exercise ZDDV on a CDC-oriented RTL block rather than a single-clock smoke test.

## RTL

- Independent write and read clock domains.
- Binary local pointers converted to Gray code before clock-domain crossing.
- Two-flop synchronizers for crossed Gray pointers.
- Full detection in the write domain.
- Empty detection in the read domain.
- Registered read data.

Default configuration:

- Data width: 8 bits.
- Depth: 8 entries.
- Write clock period: 10 ns.
- Read clock period: 14 ns.

## Verification

The self-checking testbench covers:

- Reset behavior.
- Fill-to-full.
- Blocked write while full.
- Drain-to-empty.
- Pointer wrap-around.
- Mixed seeded push/pop stress.
- End-to-end data ordering with a scoreboard queue.
- Gray-pointer one-bit transition checks in both clock domains.
- Final write/read accounting.

Run it with:

```bash
zddv --project examples/async_fifo build
zddv --project examples/async_fifo run --test async_fifo_smoke --seed 42
zddv --project examples/async_fifo regress examples/async_fifo/regression.toml
zddv --project examples/async_fifo coverage
```
