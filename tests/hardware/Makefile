PROJECT_ROOT := $(abspath ../..)
OUTDIR ?= $(PROJECT_ROOT)/out
MEMORY_NAME ?= sram_1rw
MODULE_OUTDIR ?= $(OUTDIR)/$(MEMORY_NAME)
WRAPPER_FILE ?= $(MODULE_OUTDIR)/$(MEMORY_NAME)_mbist.v
SRAM_SOURCE ?= $(abspath ./$(MEMORY_NAME).v)
WRAPPER_MODULE ?= $(MEMORY_NAME)_mbist
USE_SABOTEUR ?= 0
FAULT_MODE ?= clean
FAULTS ?= 0
FAULT_SEED ?=
FAULT_TYPE ?= stuck-at
PULSE_WIDTH_NS ?= 2
ADDR_WIDTH ?= 10
DATA_WIDTH ?= 32
ALGO ?= march-c
ALGO_DIR := $(subst -,_,$(ALGO))
ALGO_TEST ?= 0
SIM_BUILD ?= sim_build/$(ALGO_DIR)_t$(ALGO_TEST)_s$(USE_SABOTEUR)
PYTHON_BIN ?= $(PROJECT_ROOT)/venv/bin/python3
COCOTB_MAKEFILES_DIR := $(shell $(PYTHON_BIN) -c "import pathlib, cocotb_tools; print(pathlib.Path(cocotb_tools.__file__).resolve().parent / 'makefiles')")

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
export FAULTS
export FAULT_SEED
export FAULT_TYPE
export PULSE_WIDTH_NS
export ADDR_WIDTH
export DATA_WIDTH
export MEMORY_NAME
export ALGO
export ALGO_TEST

include $(COCOTB_MAKEFILES_DIR)/Makefile.sim
