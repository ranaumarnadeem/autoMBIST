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

        # `nix run github:ranaumarnadeem/autoMBIST` -- the CLI itself, as
        # opposed to devShells.default which gives you a shell to hack in.
        # The EDA tools are wrapped onto PATH rather than left to the caller:
        # `autombist test`/`simulate`/`run` shell out to verilator, iverilog,
        # yosys and make, so an unwrapped binary would install cleanly and then
        # fail at the first real command.
        autombist = python.pkgs.buildPythonApplication {
          pname = "autombist";
          version = "1.1.2";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = [ python.pkgs.hatchling pkgs.makeWrapper ];
          propagatedBuildInputs = with python.pkgs; [
            jinja2
            pyyaml
            typer
            cocotb        # 2.x bundles cocotb_tools in the same wheel
            tkinter       # `autombist shell`; import-guarded in tcl_shell.py,
                          # so omitting it only silently disables that one
                          # command -- which `doctor` reported as MISSING under
                          # `nix run` before this was added.
          ];

          # nixpkgs has no separate cocotb-tools attribute -- cocotb 2.x ships
          # cocotb_tools itself (same reasoning as the devShell above), so the
          # metadata pin has nothing to resolve against.
          pythonRelaxDeps = [ "cocotb-tools" ];
          pythonRemoveDeps = [ "cocotb-tools" ];

          # The test suite needs verilator and a writable cache; it runs in CI
          # via `nix develop`, not as part of building the package.
          doCheck = false;
          pythonImportsCheck = [ "autombist" ];

          postFixup = ''
            wrapProgram $out/bin/autombist               --prefix PATH : ${pkgs.lib.makeBinPath [
                pkgs.verilator pkgs.iverilog pkgs.yosys pkgs.gnumake pkgs.gcc
              ]}
          '';

          meta = {
            description = "MBIST wrapper and fault-flow generator";
            homepage = "https://github.com/ranaumarnadeem/autoMBIST";
            license = pkgs.lib.licenses.asl20;
            mainProgram = "autombist";
          };
        };
      in
      {
        packages.default = autombist;
        packages.autombist = autombist;

        apps.default = {
          type = "app";
          program = "${autombist}/bin/autombist";
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.verilator
            pkgs.iverilog
            pkgs.yosys
            pkgs.gnumake   # runner.py drives cocotb's Makefile.sim via `make`
            pkgs.gcc       # verilator emits C++ that must be compiled
            pkgs.ccache    # verilated.mk reads $OBJCACHE, empty by default (see below)
          ];

          # Resolve `import autombist` from the source tree (mirrors the
          # PYTHONPATH the hardware Makefile already sets) so no editable pip
          # install is needed; runner.py then drives the cocotb Makefile with
          # PYTHON_BIN=sys.executable = this env's python (which has cocotb).
          #
          # OBJCACHE=ccache: verilator --binary's generated verilated.mk calls
          # `$(OBJCACHE) $(CXX) ... -c -o $@ $<` for every compile step
          # (verilated.mk:269ff) -- OBJCACHE is verilator's own, built-in hook
          # for exactly this, empty (a no-op prefix) by default. This is a
          # different, complementary layer from algo_engine.py's own
          # AUTOMBIST_ENGINE_CACHE: that one skips calling verilator AT ALL on
          # an exact content-hash match; ccache still helps on a hash MISS
          # (e.g. a different memory geometry) wherever a shared translation
          # unit -- verilator's own runtime library sources, not this
          # project's per-design generated code -- compiles identically
          # regardless of which design triggered the build. Verified locally
          # against this project's real fault_ram.sv/march_engine.sv: a
          # from-scratch rebuild of identical sources went from 4/4 ccache
          # misses (cold) to 4/4 hits (warm). CCACHE_DIR is left at ccache's
          # own default ($XDG_CACHE_HOME/ccache, ~/.cache/ccache) rather than
          # pinned here, so CI's cache step (test.yml) and local dev share the
          # same convention with nothing to keep in sync.
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            export OBJCACHE=ccache
            echo "autombist toolchain:"
            echo "  verilator $(verilator --version 2>/dev/null | head -1)"
            echo "  $(iverilog -V 2>/dev/null | head -1)"
            echo "  yosys $(yosys --version 2>/dev/null | head -1)"
            echo "  cocotb $(python -c 'import cocotb; print(cocotb.__version__)' 2>/dev/null)"
            echo "  ccache $(ccache --version 2>/dev/null | head -1)"
          '';
        };
      });
}
