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
PYTHON_BIN ?= $(PROJECT_ROOT)/venv/bin/python3
COCOTB_MAKEFILES_DIR := $(shell $(PYTHON_BIN) -c "import pathlib, cocotb_tools; print(pathlib.Path(cocotb_tools.__file__).resolve().parent / 'makefiles')")

ifeq ($(USE_SABOTEUR),1)
SABOTEUR_SOURCE := $(MODULE_OUTDIR)/$(MEMORY_NAME)_saboteur.v
else
SABOTEUR_SOURCE :=
endif

VERILOG_SOURCES := $(WRAPPER_FILE) \
                   $(SABOTEUR_SOURCE) \
                   $(SRAM_SOURCE) \
                   $(MODULE_OUTDIR)/mbist_algo.sv \
                   $(MODULE_OUTDIR)/mbist_fsm.sv \
                   $(MODULE_OUTDIR)/mbist_top.sv

TOPLEVEL ?= $(WRAPPER_MODULE)
COCOTB_TEST_MODULES ?= test_mbist
SIM ?= icarus
TOPLEVEL_LANG ?= verilog

COMPILE_ARGS += -g2012
export PYTHONPATH := $(PROJECT_ROOT)/src:$(PYTHONPATH)
export PYTHON_BIN
export FAULT_MODE
export FAULTS
export FAULT_SEED
export MEMORY_NAME

include $(COCOTB_MAKEFILES_DIR)/Makefile.sim
