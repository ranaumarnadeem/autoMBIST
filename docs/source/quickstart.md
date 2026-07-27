# Quickstart

This gets you from a fresh environment to a first generate + simulate run.

```bash
# 1. Inside WSL/Linux, from the repo root (see Installation):
nix develop

# 2. Scaffold a starter config in the current directory
autombist init --out .

# 3. Generate the MBIST wrapper + RTL for the memory described in config.yml
autombist generate --config config.yml --out out

# 4. Simulate the generated design with cocotb + Icarus
autombist simulate --out out/<memory_name>

# ...or do steps 3+4 in one shot:
autombist run --config config.yml --out out
```

`<memory_name>` is the `memory_name` field from your config file — each
memory gets its own subdirectory under `out/`.

## Inject faults and grade coverage

```bash
autombist generate --config config.yml --out out \
    --test --faults 50 --seed 1234 --algo march-c --fault-type stuck-at
autombist simulate --out out/<memory_name>
```

## Explore the research platform (no macro needed)

```bash
autombist test --addr-width 8 --data-width 8 --algo march_c --faults faults.txt
autombist algo   # interactive research shell
```

## Next steps

- Have a real memory to test? See {doc}`example` for a full walkthrough,
  including redundancy repair.
- Want to harden a design to GDS? See {doc}`librelane`.
- Full flag-by-flag reference: {doc}`cli-reference`.
