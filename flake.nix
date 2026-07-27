{
  description = "autombist: MBIST wrapper generator + cocotb/Verilator verification toolchain";

  inputs = {
    # Pin to a channel that ships the tool versions autombist actually needs:
    #   iverilog 13.0   (the march-1r1w/march-2rw RTL passes unpacked arrays
    #                    through module ports -- a feature only in iverilog 13.x;
    #                    the 12.0 release the CI runners' apt ships cannot compile it)
    #   verilator 5.048 (>= 5.020 for --binary/--timing + queues/foreach/final;
    #                    STRUCTSEL-safe)
    #   yosys 0.62
    #   python3Packages.cocotb 2.0.1 (cocotb 2.x bundles cocotb_tools in-wheel)
    # flake.lock pins the exact commit so CI and local dev get the identical closure.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # cocotb 2.0.1 supports Python up to 3.13; pin 3.11 explicitly rather
        # than a bare python3 that could resolve to >= 3.14 (where the nixpkgs
        # cocotb package is disabled).
        python = pkgs.python311;

        pythonEnv = python.withPackages (ps: with ps; [
          cocotb        # 2.x -- provides cocotb_tools/cocotb-config in the same wheel
          pytest
          pytest-cov
          typer
          jinja2
          pyyaml
          tkinter       # stdlib Tcl binding for `autombist shell` (Workstream C-D);
                         # import-guarded everywhere else, so its absence never
                         # breaks the rest of the CLI (see src/autombist/tcl_shell.py)
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.verilator
            pkgs.iverilog
            pkgs.yosys
            pkgs.gnumake   # runner.py drives cocotb's Makefile.sim via `make`
            pkgs.gcc       # verilator emits C++ that must be compiled
          ];

          # Resolve `import autombist` from the source tree (mirrors the
          # PYTHONPATH the hardware Makefile already sets) so no editable pip
          # install is needed; runner.py then drives the cocotb Makefile with
          # PYTHON_BIN=sys.executable = this env's python (which has cocotb).
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            echo "autombist toolchain:"
            echo "  verilator $(verilator --version 2>/dev/null | head -1)"
            echo "  $(iverilog -V 2>/dev/null | head -1)"
            echo "  yosys $(yosys --version 2>/dev/null | head -1)"
            echo "  cocotb $(python -c 'import cocotb; print(cocotb.__version__)' 2>/dev/null)"
          '';
        };
      });
}
