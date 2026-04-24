#!/usr/bin/env python3
"""Reconfigurable SRAM synthesis helper for OpenRAM."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def run_cmd(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True)


def run_cmd_with_retry(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    retries: int,
    delay_seconds: int = 5,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            print(f"Attempt {attempt}/{retries}")
            run_cmd(cmd, cwd, env)
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if attempt == retries:
                break
            print(f"Command failed (attempt {attempt}). Retrying in {delay_seconds}s...")
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error


def build_output_name(args: argparse.Namespace) -> str:
    ports = f"{args.num_rw_ports}rw{args.num_r_ports}r{args.num_w_ports}w"
    if args.output_name:
        return args.output_name
    if args.tech == "sky130":
        return (
            f"{args.tech}_sram_{ports}_{args.word_size}x{args.num_words}_"
            f"w{args.write_size}"
        )
    return f"{args.tech}_sram_{ports}_{args.word_size}x{args.num_words}"


def default_supply(tech: str) -> float:
    if tech == "scn4m_subm":
        return 5.0
    if tech == "freepdk45":
        return 1.0
    return 1.8


def build_config_text(args: argparse.Namespace, output_name: str) -> str:
    lines = [
        f"word_size = {args.word_size}",
        f"num_words = {args.num_words}",
        f"num_rw_ports = {args.num_rw_ports}",
        f"num_r_ports = {args.num_r_ports}",
        f"num_w_ports = {args.num_w_ports}",
        f"tech_name = \"{args.tech}\"",
        "process_corners = [\"TT\"]",
        f"supply_voltages = [{args.supply_voltage}]",
        "temperatures = [25]",
        f"check_lvsdrc = {args.run_drc_lvs}",
    ]

    if args.tech == "sky130":
        lines.extend(
            [
                "nominal_corner_only = True",
                "route_supplies = \"ring\"",
                "uniquify = True",
                f"write_size = {args.write_size}",
                f"num_spare_rows = {args.num_spare_rows}",
                f"num_spare_cols = {args.num_spare_cols}",
            ]
        )
    else:
        lines.extend(
            [
                "nominal_corner_only = False",
                "route_supplies = \"side\"",
            ]
        )

    lines.extend(
        [
            f"output_name = \"{output_name}\"",
            f"output_path = r\"{args.output_path}\"",
            "",
        ]
    )

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SRAMs with OpenRAM")
    parser.add_argument("--tech", default="scn4m_subm", choices=["scn4m_subm", "sky130", "freepdk45"])
    parser.add_argument("--word-size", type=int, default=32)
    parser.add_argument("--num-words", type=int, default=256)
    parser.add_argument("--num-rw-ports", type=int, default=1)
    parser.add_argument("--num-r-ports", type=int, default=0)
    parser.add_argument("--num-w-ports", type=int, default=0)
    parser.add_argument("--write-size", type=int)
    parser.add_argument("--num-spare-rows", type=int, default=1)
    parser.add_argument("--num-spare-cols", type=int, default=1)
    parser.add_argument("--supply-voltage", type=float)
    parser.add_argument("--output-name")
    parser.add_argument("--setup-sky130", action="store_true")
    parser.add_argument("--sky130-setup-retries", type=int, default=5)
    parser.add_argument("--run-drc-lvs", action="store_true")
    parser.add_argument("--keep-config", action="store_true")
    parser.add_argument("--openram-dir")
    parser.add_argument("--pdk-root")
    parser.add_argument("--output-root")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    openram_dir = Path(args.openram_dir).resolve() if args.openram_dir else repo_root / "OpenRAM"

    if not openram_dir.is_dir():
        print(f"OpenRAM directory not found: {openram_dir}", file=sys.stderr)
        return 1

    if args.word_size <= 0 or args.num_words <= 0:
        print("word-size and num-words must be positive integers", file=sys.stderr)
        return 1

    if args.write_size is None:
        args.write_size = 8 if args.tech == "sky130" and args.word_size >= 8 else args.word_size

    if args.word_size % args.write_size != 0:
        print("word-size must be divisible by write-size", file=sys.stderr)
        return 1

    if args.supply_voltage is None:
        args.supply_voltage = default_supply(args.tech)

    output_name = build_output_name(args)

    output_root = Path(args.output_root).resolve() if args.output_root else (repo_root / "input")
    output_root.mkdir(parents=True, exist_ok=True)
    output_dir = output_root / output_name
    args.output_path = output_dir.as_posix()

    generated_cfg_dir = openram_dir / "macros" / "generated_configs"
    generated_cfg_dir.mkdir(parents=True, exist_ok=True)
    config_path = generated_cfg_dir / f"{output_name}.py"
    config_path.write_text(build_config_text(args, output_name), encoding="utf-8")

    env = os.environ.copy()
    env["OPENRAM_HOME"] = str(openram_dir / "compiler")
    env["OPENRAM_TECH"] = str(openram_dir / "technology")
    env["PYTHONPATH"] = env["OPENRAM_HOME"]
    if args.pdk_root:
        pdk_root = Path(args.pdk_root).resolve()
    elif Path("/pdk").exists():
        pdk_root = Path("/pdk")
    elif (repo_root / "pdk").exists():
        pdk_root = (repo_root / "pdk").resolve()
    else:
        pdk_root = openram_dir
    env["PDK_ROOT"] = str(pdk_root)

    miniconda_bin = openram_dir / "miniconda" / "bin"
    python_bin = miniconda_bin / "python3"
    if miniconda_bin.is_dir():
        env["PATH"] = f"{miniconda_bin}:{env.get('PATH', '')}"

    if args.sky130_setup_retries < 1:
        print("sky130-setup-retries must be >= 1", file=sys.stderr)
        return 1

    if args.tech == "sky130" or args.setup_sky130:
        sky130_libs_tech = pdk_root / "sky130A" / "libs.tech"
        need_setup = args.setup_sky130 or not sky130_libs_tech.exists()
        if need_setup:
            print("Preparing Sky130 OpenRAM collateral...")
            skywater_pdk = openram_dir / "skywater-pdk"
            if skywater_pdk.exists():
                # Improve robustness on unstable links during large submodule fetches.
                run_cmd(["git", "config", "http.version", "HTTP/1.1"], skywater_pdk, env)
                run_cmd(["git", "config", "http.postBuffer", "524288000"], skywater_pdk, env)
                run_cmd(["git", "config", "core.compression", "0"], skywater_pdk, env)

            try:
                run_cmd_with_retry(
                    ["make", "sky130-pdk"],
                    openram_dir,
                    env,
                    retries=args.sky130_setup_retries,
                )
            except subprocess.CalledProcessError as exc:
                if not sky130_libs_tech.exists():
                    print(
                        "Sky130 PDK setup failed and sky130A is still missing. "
                        "Retry later with --setup-sky130, or pass an existing PDK root with --pdk-root.",
                        file=sys.stderr,
                    )
                    return exc.returncode

            run_cmd_with_retry(
                ["make", "sky130-install"],
                openram_dir,
                env,
                retries=args.sky130_setup_retries,
            )

        sky130_spice = pdk_root / "sky130A" / "libs.tech" / "ngspice" / "sky130.lib.spice"
        if not sky130_spice.exists():
            print(
                f"Sky130 tech files missing at {sky130_spice}. "
                "Run with --setup-sky130 or provide --pdk-root to an existing sky130A installation.",
                file=sys.stderr,
            )
            return 1

    compiler = str(python_bin if python_bin.exists() else Path(sys.executable))
    cmd = [compiler, str(openram_dir / "sram_compiler.py")]
    if not args.run_drc_lvs:
        cmd.append("-n")
    if args.verbose:
        cmd.append("-v")
    cmd.append(str(config_path))

    run_cmd(cmd, openram_dir, env)

    if not args.keep_config:
        config_path.unlink(missing_ok=True)

    print(f"SRAM generation complete: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
