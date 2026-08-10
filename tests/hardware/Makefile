PROJECT_ROOT := $(abspath ../..)
OUTDIR ?= $(PROJECT_ROOT)/out
MEMORY_NAME ?= sram_1rw
MODULE_OUTDIR ?= $(OUTDIR)/$(MEMORY_NAME)
WRAPPER_FILE ?= $(MODULE_OUTDIR)/$(MEMORY_NAME)_mbist.v
SRAM_SOURCE ?= $(abspath ./$(MEMORY_NAME).v)
WRAPPER_MODULE ?= $(MEMORY_NAME)_mbist
USE_SABOTEUR ?= 0
USE_REPAIR_REMAP ?= 0
FAULT_MODE ?= clean
FAULTS ?= 0
FAULT_SEED ?=
FAULT_TYPE ?= stuck-at
PULSE_WIDTH_NS ?= 2
ADDR_WIDTH ?= 10
DATA_WIDTH ?= 32
READ_LATENCY ?= 1
ALGO ?= march-c
ALGO_DIR := $(subst -,_,$(ALGO))
ALGO_TEST ?= 0
# Nested under MODULE_OUTDIR (unique per generated wrapper -- a fresh
# pytest tmp_path each e2e test invocation, or a stable out/<memory> dir for
# CLI use) rather than a bare relative path shared across every invocation
# of a given algo/saboteur combination. A shared path let Make's mtime-based
# staleness check silently reuse a stale compiled sim.vvp from a PRIOR
# tmp_path's generated sources (different fault content, sometimes even a
# different memory) when timestamps didn't clearly show the new sources as
# newer -- causing real false negatives (undetected faults) and at least one
# observed false positive (bist_fail=1 with zero injected faults). Keying by
# MODULE_OUTDIR makes that collision structurally impossible while still
# preserving legitimate incremental-rebuild caching within one fixed output
# directory.
SIM_BUILD ?= $(MODULE_OUTDIR)/sim_build/$(ALGO_DIR)_t$(ALGO_TEST)_s$(USE_SABOTEUR)
PYTHON_BIN ?= $(PROJECT_ROOT)/venv/bin/python3
COCOTB_MAKEFILES_DIR := $(shell $(PYTHON_BIN) -c "import pathlib, cocotb_tools; print(pathlib.Path(cocotb_tools.__file__).resolve().parent / 'makefiles')")
COCOTB_RESULTS_FILE ?= $(MODULE_OUTDIR)/results.xml

ifeq ($(ALGO_TEST),1)
SRAM_SOURCE :=
ALGO_PROBE_SOURCE := $(abspath ./algo_module_probe.sv)
else
SRAM_SOURCE ?= $(abspath ./$(MEMORY_NAME).v)
ALGO_PROBE_SOURCE :=
endif

ifeq ($(USE_SABOTEUR),1)
SABOTEUR_SOURCE := $(MODULE_OUTDIR)/$(MEMORY_NAME)_saboteur.v
else
SABOTEUR_SOURCE :=
endif

# External row-repair remap RTL (redundancy builds only). copy_mbist_rtl copies
# rtl/*.sv into MODULE_OUTDIR alongside the algo RTL, including the Step-E
# on-chip analyzer/sequencer -- harmless to compile-but-not-instantiate for a
# tester-driven (non-onchip_selfrepair) redundancy build, same as every other
# option in this file that coexists unused in VERILOG_SOURCES.
ifeq ($(USE_REPAIR_REMAP),1)
REMAP_SOURCE := $(MODULE_OUTDIR)/repair_remap_row.sv \
                $(MODULE_OUTDIR)/repair_remap_col.sv \
                $(MODULE_OUTDIR)/onchip_row_repair_analyzer.sv \
                $(MODULE_OUTDIR)/onchip_selfrepair_ctrl.sv
else
REMAP_SOURCE :=
endif

ifeq ($(ALGO_TEST),1)
VERILOG_SOURCES := $(SRAM_SOURCE) \
                   $(ALGO_PROBE_SOURCE) \
                   $(MODULE_OUTDIR)/$(ALGO_DIR)/$(ALGO_DIR)_algo.sv \
                   $(MODULE_OUTDIR)/$(ALGO_DIR)/$(ALGO_DIR)_fsm.sv \
                   $(MODULE_OUTDIR)/$(ALGO_DIR)/$(ALGO_DIR)_top.sv
TOPLEVEL ?= algo_module_probe
COCOTB_TEST_MODULES ?= test_algo_modules
ifeq ($(ALGO),march-raw)
COMPILE_ARGS += -DALGO_MARCH_RAW
endif
else
VERILOG_SOURCES := $(WRAPPER_FILE) \
                   $(SABOTEUR_SOURCE) \
                   $(REMAP_SOURCE) \
                   $(SRAM_SOURCE) \
                   $(MODULE_OUTDIR)/$(ALGO_DIR)/$(ALGO_DIR)_algo.sv \
                   $(MODULE_OUTDIR)/$(ALGO_DIR)/$(ALGO_DIR)_fsm.sv \
                   $(MODULE_OUTDIR)/$(ALGO_DIR)/$(ALGO_DIR)_top.sv
TOPLEVEL ?= $(WRAPPER_MODULE)
COCOTB_TEST_MODULES ?= test_mbist
endif
SIM ?= icarus
TOPLEVEL_LANG ?= verilog

COMPILE_ARGS += -g2012
export PYTHONPATH := $(PROJECT_ROOT)/src:$(PYTHONPATH)
export PYTHON_BIN
export FAULT_MODE
export REPAIR_PHASE
export FAULTS
export FAULT_SEED
export FAULT_TYPE
export PULSE_WIDTH_NS
export ADDR_WIDTH
export DATA_WIDTH
export READ_LATENCY
export MEMORY_NAME
export MODULE_OUTDIR
export ALGO
export ALGO_TEST
export COCOTB_RESULTS_FILE

include $(COCOTB_MAKEFILES_DIR)/Makefile.sim
