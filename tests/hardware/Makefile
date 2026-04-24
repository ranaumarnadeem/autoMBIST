PROJECT_ROOT := $(abspath ../..)
OUTDIR ?= $(PROJECT_ROOT)/out
MEMORY_NAME ?= sram_1rw
WRAPPER_FILE ?= $(OUTDIR)/$(MEMORY_NAME)_mbist.v
SRAM_SOURCE ?= $(abspath ./sram_1rw.v)
WRAPPER_MODULE ?= sram_1rw_mbist
PYTHON_BIN ?= $(PROJECT_ROOT)/venv/bin/python3
COCOTB_MAKEFILES_DIR := $(shell $(PYTHON_BIN) -c "import pathlib, cocotb_tools; print(pathlib.Path(cocotb_tools.__file__).resolve().parent / 'makefiles')")

VERILOG_SOURCES := $(WRAPPER_FILE) \
                   $(SRAM_SOURCE) \
                   $(OUTDIR)/mbist_algo.sv \
                   $(OUTDIR)/mbist_fsm.sv \
                   $(OUTDIR)/mbist_top.sv

TOPLEVEL ?= $(WRAPPER_MODULE)
COCOTB_TEST_MODULES ?= test_mbist
SIM ?= icarus
TOPLEVEL_LANG ?= verilog

COMPILE_ARGS += -g2012
export PYTHONPATH := $(PROJECT_ROOT):$(PYTHONPATH)
export PYTHON_BIN

include $(COCOTB_MAKEFILES_DIR)/Makefile.sim
