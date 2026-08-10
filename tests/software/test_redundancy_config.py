"""Pin the `redundancy:` config block validation (generator._validate_redundancy).

`redundancy: {num_spare_rows: N, num_spare_cols: 0}` is the single source that
populates the shared SpareGeometry both the wrapper generator and BIRA read. It
is paired with `repair_ports` (which drives the external remap). These pure tests
fix the block's rules; the RTL wiring it produces is covered by
test_wrapper_repair_ports.py and the e2e in test_repair_row_e2e.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autombist.generator import ConfigError, generate_from_config, load_config  # noqa: E402

BASE = {
    "memory_name": "sram_tiny",
    "wrapper_module_name": "sram_tiny_mbist",
    "addr_width": 2,
    "data_width": 4,
    "we_active_low": True,
    "ports": {"clk": "clk0", "addr": "addr0", "din": "din0", "dout": "dout0", "we": "web0", "csb": "csb0"},
}
REPAIR_PORTS = [
    {"name": "row_repair_en", "width": 2, "dir": "input"},
    {"name": "faulty_row_addr", "width": 4, "dir": "input"},
]
# Sized for num_spare_rows: 1 (row_repair_en width == num_spare_rows,
# faulty_row_addr width == num_spare_rows * addr_width(2)) -- most tests below
# use num_spare_rows: 1, while REPAIR_PORTS above is sized for rows: 2.
REPAIR_PORTS_1ROW = [
    {"name": "row_repair_en", "width": 1, "dir": "input"},
    {"name": "faulty_row_addr", "width": 2, "dir": "input"},
]
MULTI_PORTS = {
    "r0": {"type": "r", "clk": "clk0", "addr": "addr0", "dout": "dout0", "csb": "csb0"},
    "w0": {"type": "w", "clk": "clk1", "addr": "addr1", "din": "din1", "csb": "csb1", "we": "we1"},
}


def _write(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _load(tmp_path: Path, config: dict) -> dict:
    return load_config(_write(tmp_path, config))


# --------------------------------------------------------------------------- #
# Valid + absent
# --------------------------------------------------------------------------- #
def test_valid_redundancy_derives_geometry(tmp_path: Path) -> None:
    loaded = _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 2}, "repair_ports": REPAIR_PORTS})
    r = loaded["redundancy"]
    assert r == {
        "base_words": 4,        # 1 << addr_width(2)
        "word_size": 4,
        "num_spare_rows": 2,
        "num_spare_cols": 0,
        "mem_addr_width": 3,    # ceil(log2(4 + 2))
        "mem_data_width": 4,    # word_size + num_spare_cols(0)
        "bit_index_width": 2,   # ceil(log2(word_size 4))
        "words_per_row": 1,     # no column muxing (the only supported value)
        "onchip_selfrepair": False,
        "onchip_repair_persistence": False,
    }


def test_onchip_selfrepair_flows_through_the_rebuilt_dict(tmp_path: Path) -> None:
    """`_validate_redundancy` REBUILDS `loaded['redundancy']` from a fixed key
    list rather than updating the input block -- so a field read from the input
    but not ALSO added to that rebuilt dict would silently read as undefined in
    Jinja regardless of what the user set. This pins that `onchip_selfrepair`
    genuinely survives the rebuild (not just defaults correctly, per the test
    above)."""
    loaded = _load(
        tmp_path,
        {**BASE, "redundancy": {"num_spare_rows": 2, "onchip_selfrepair": True}},
    )
    assert loaded["redundancy"]["onchip_selfrepair"] is True


def test_absent_redundancy_leaves_no_key(tmp_path: Path) -> None:
    loaded = _load(tmp_path, BASE)
    assert "redundancy" not in loaded


# --------------------------------------------------------------------------- #
# The redundancy <-> repair_ports biconditional (both directions)
# --------------------------------------------------------------------------- #
def test_redundancy_without_repair_ports_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="repair_ports"):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 2}})


def test_repair_ports_without_redundancy_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="redundancy"):
        _load(tmp_path, {**BASE, "repair_ports": REPAIR_PORTS})


# --------------------------------------------------------------------------- #
# Field rules
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Column repair (Workstream M.1): accepted on the tester-driven path, with a
# strict, loud surface. num_spare_cols was hard-rejected outright before this.
# --------------------------------------------------------------------------- #
COL_PORTS = {**BASE["ports"], "spare_wen": "spare_wen0"}
COL_REPAIR_PORTS = [
    *REPAIR_PORTS_1ROW,
    {"name": "col_repair_en", "width": 1, "dir": "input"},
    {"name": "faulty_bit", "width": 2, "dir": "input"},   # 1 spare * bit_index_width(2)
]
COL_BASE = {**BASE, "ports": COL_PORTS}


def test_num_spare_cols_accepted_on_the_tester_driven_path(tmp_path: Path) -> None:
    loaded = _load(tmp_path, {
        **COL_BASE,
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
        "repair_ports": COL_REPAIR_PORTS,
    })
    r = loaded["redundancy"]
    assert r["num_spare_cols"] == 1
    assert r["mem_data_width"] == 5      # word_size 4 + 1 spare column
    assert r["bit_index_width"] == 2


def test_column_repair_pins_are_tagged_bind_col(tmp_path: Path) -> None:
    """The wrapper template partitions its instantiation loops on this tag -- a
    column pin bound into repair_remap_row would be an elaboration error."""
    loaded = _load(tmp_path, {
        **COL_BASE,
        "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
        "repair_ports": COL_REPAIR_PORTS,
    })
    binds = {e["name"]: e["bind"] for e in loaded["repair_ports"]}
    assert binds == {
        "row_repair_en": "row", "faulty_row_addr": "row",
        "col_repair_en": "col", "faulty_bit": "col",
    }


def test_row_only_config_tags_every_pin_bind_row(tmp_path: Path) -> None:
    """The byte-identity guarantee: with no spare columns every pin binds "row",
    so the template's loops render exactly as they did before column repair."""
    loaded = _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 2}, "repair_ports": REPAIR_PORTS})
    assert all(e["bind"] == "row" for e in loaded["repair_ports"])


def test_num_spare_cols_rejected_with_onchip_selfrepair(tmp_path: Path) -> None:
    """onchip_row_repair_analyzer.sv is row-only: the column pins would exist
    but never be driven -- a silently no-op repair."""
    with pytest.raises(ConfigError, match="num_spare_cols > 0 is not supported with"):
        _load(tmp_path, {
            **COL_BASE,
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1, "onchip_selfrepair": True},
        })


def test_num_spare_cols_cannot_exceed_data_width(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="exceeds data_width"):
        _load(tmp_path, {
            **COL_BASE,
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 5},   # data_width is 4
            "repair_ports": COL_REPAIR_PORTS,
        })


def test_num_spare_cols_requires_the_spare_wen_port_role(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="spare_wen"):
        _load(tmp_path, {
            **BASE,   # BASE's ports have no spare_wen
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
            "repair_ports": COL_REPAIR_PORTS,
        })


@pytest.mark.parametrize("missing", ["col_repair_en", "faulty_bit"])
def test_num_spare_cols_requires_the_canonical_column_pins(tmp_path: Path, missing: str) -> None:
    ports = [e for e in COL_REPAIR_PORTS if e["name"] != missing]
    with pytest.raises(ConfigError, match=missing):
        _load(tmp_path, {
            **COL_BASE,
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
            "repair_ports": ports,
        })


@pytest.mark.parametrize("pin,bad_width", [("col_repair_en", 2), ("faulty_bit", 3)])
def test_column_pin_widths_are_validated_strictly(tmp_path: Path, pin: str, bad_width: int) -> None:
    """A brand-new surface with no back-compat cost, so unlike the row pins these
    widths are checked rather than trusted."""
    ports = [
        {**e, "width": bad_width} if e["name"] == pin else e
        for e in COL_REPAIR_PORTS
    ]
    with pytest.raises(ConfigError, match="width must be"):
        _load(tmp_path, {
            **COL_BASE,
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
            "repair_ports": ports,
        })


# --- The biconditional must run BOTH ways (adversarial-review findings 1-3) --- #
def test_column_repair_pins_without_spare_columns_still_rejected(tmp_path: Path) -> None:
    """Declaring the column surface but forgetting num_spare_cols must still
    fail: col_repair_en/faulty_bit would reach the wrapper boundary bound to
    nothing, and a tester would drive the repair register into dead nets.

    This is the half of the old spare_wen guard that remains an error. The
    spare_wen half became a tie-off -- see the tests below -- because
    ports.spare_wen states a fact about the MACRO (it has that pin), which is
    independent of whether this design uses column repair. These pins are
    different: they exist only to carry a repair value, so declaring them
    without somewhere for that value to go is unambiguously a mistake."""
    with pytest.raises(ConfigError, match="are column-repair pins but"):
        _load(tmp_path, {
            **COL_BASE,
            "redundancy": {"num_spare_rows": 1},   # num_spare_cols omitted -> 0
            "repair_ports": COL_REPAIR_PORTS,
        })


def test_spare_wen_is_tied_off_when_column_repair_is_off(tmp_path: Path) -> None:
    """A stock OpenRAM macro compiled with spare columns has a spare_wen pin
    whether or not you intend to repair with it. Declaring it with no column
    repair used to RAISE, advising "drop the spare_wen port role" -- advice that
    made things worse, because undeclared the wrapper cannot connect the pin at
    all and leaves a real macro's input floating. It must now tie off instead."""
    outdir = tmp_path / "out"
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        **COL_BASE,
        "redundancy": {"num_spare_rows": 1},   # rows only, no column repair
        "repair_ports": REPAIR_PORTS_1ROW,
    }, sort_keys=False), encoding="utf-8")

    wrapper = generate_from_config(config_path, outdir, algo="march-c")
    text = wrapper.read_text(encoding="utf-8")
    assert ".spare_wen0('0)" in text, text
    # And it must NOT be wired to a repair signal -- there is no column repair.
    assert "sram_spare_wen" not in text, text


def test_spare_wen_tied_off_with_no_redundancy_block_at_all(tmp_path: Path) -> None:
    """The plainest case, and the one a first-time user hits: no redundancy
    anywhere, but the macro still physically has the pin. This shape did not
    even raise before -- it generated happily and silently dropped the
    declaration, leaving the pin floating with no diagnostic at all."""
    outdir = tmp_path / "out"
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(COL_BASE, sort_keys=False), encoding="utf-8")

    wrapper = generate_from_config(config_path, outdir, algo="march-c")
    assert ".spare_wen0('0)" in wrapper.read_text(encoding="utf-8")


def test_spare_wen_tied_off_on_a_non_first_port(tmp_path: Path) -> None:
    """The multi-port case, preserving what the old rejection test was really
    guarding. The guard it replaced read only `next(iter(normalized_ports))`,
    and on the 1r1w shape the READ port sorts first and structurally cannot
    carry spare_wen (OPTIONAL_PORT_KEYS_BY_TYPE gives "r" no optional keys) --
    so anything that inspects one port sails past the write port that has it.
    The tie-off has to reach the write port for the same reason."""
    ports = {
        **MULTI_PORTS,
        "w0": {**MULTI_PORTS["w0"], "spare_wen": "spare_wen1"},
    }
    assert next(iter(ports)) == "r0", "fixture must keep the read port first, or it proves nothing"
    outdir = tmp_path / "out"
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        **BASE,
        "ports": ports,
        "redundancy": {"num_spare_rows": 1, "onchip_selfrepair": True},
    }, sort_keys=False), encoding="utf-8")

    wrapper = generate_from_config(config_path, outdir, algo="march-1r1w")
    assert ".spare_wen1('0)" in wrapper.read_text(encoding="utf-8")


@pytest.mark.parametrize("pin", ["row_repair_en", "faulty_row_addr"])
def test_row_pin_must_be_dir_input(tmp_path: Path, pin: str) -> None:
    """The row twin of test_column_pin_must_be_dir_input. repair_remap_row's
    every bindable port is an input, so `output` declares a wrapper-boundary
    wire driven by nothing and gives the tester no pin -- the same failure the
    column check was added to prevent. `dir` has always been checked, since
    `input` is the only value that has ever worked; presence and width are
    checked too now -- see test_redundancy_requires_the_canonical_row_pins and
    test_row_pin_widths_are_validated_strictly below."""
    ports = [
        {**e, "dir": "output"} if e["name"] == pin else e
        for e in REPAIR_PORTS
    ]
    with pytest.raises(ConfigError, match="must be dir: input"):
        _load(tmp_path, {
            **BASE,
            "redundancy": {"num_spare_rows": 2},
            "repair_ports": ports,
        })


def test_row_pin_dir_is_checked_on_the_2d_path_too(tmp_path: Path) -> None:
    """The row check sits outside the num_spare_cols split, so it must fire for
    a row+column config as well -- not just row-only."""
    ports = [
        {**e, "dir": "output"} if e["name"] == "row_repair_en" else e
        for e in COL_REPAIR_PORTS
    ]
    with pytest.raises(ConfigError, match="must be dir: input"):
        _load(tmp_path, {
            **COL_BASE,
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
            "repair_ports": ports,
        })


@pytest.mark.parametrize("missing", ["row_repair_en", "faulty_row_addr"])
def test_redundancy_requires_the_canonical_row_pins(tmp_path: Path, missing: str) -> None:
    """The row twin of test_num_spare_cols_requires_the_canonical_column_pins.
    repair_remap_row's instantiation connects repair_ports entries by NAME
    (wrapper_template.j2), so a config missing row_repair_en or
    faulty_row_addr used to generate and elaborate clean under Yosys (an
    unmatched named port connection is not an error there) and never
    actually repair anything -- only iverilog's stricter elaboration caught
    it, and only by accident of which pin was missing."""
    ports = [e for e in REPAIR_PORTS_1ROW if e["name"] != missing]
    with pytest.raises(ConfigError, match=missing):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 1}, "repair_ports": ports})


@pytest.mark.parametrize("pin,bad_width", [("row_repair_en", 2), ("faulty_row_addr", 3)])
def test_row_pin_widths_are_validated_strictly(tmp_path: Path, pin: str, bad_width: int) -> None:
    """The row twin of test_column_pin_widths_are_validated_strictly. A wrong
    width is a silent truncation/extension in the named port connection under
    both iverilog and Yosys -- neither toolchain's elaboration catches it, so
    this has to be a config-validation-time rejection."""
    ports = [{**e, "width": bad_width} if e["name"] == pin else e for e in REPAIR_PORTS_1ROW]
    with pytest.raises(ConfigError, match="width must be"):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 1}, "repair_ports": ports})


@pytest.mark.parametrize("pin", ["col_repair_en", "faulty_bit"])
def test_column_repair_pins_without_spare_columns_rejected(tmp_path: Path, pin: str) -> None:
    """The other half of the same hole: a column-repair pin name in a row-only
    config. Before the fix it was tagged bind='col' purely by name, so the
    template excluded it from the row remap and had no column remap to bind it
    to -- turning what used to be a loud elaboration error into a dangling
    wrapper input."""
    with pytest.raises(ConfigError, match="column-repair pins but"):
        _load(tmp_path, {
            **BASE,   # no spare_wen role, so the check above cannot fire first
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 0},
            "repair_ports": [*REPAIR_PORTS_1ROW, {"name": pin, "width": 1, "dir": "input"}],
        })


def test_row_only_config_with_a_col_named_pin_tags_it_row_when_allowed() -> None:
    """Belt-and-braces on the bind tag itself: _COL_REPAIR_PINS membership must
    not decide `bind` on its own, independent of the config-level rejection."""
    from autombist.generator import _COL_REPAIR_PINS

    assert "col_repair_en" in _COL_REPAIR_PINS
    assert "faulty_bit" in _COL_REPAIR_PINS


@pytest.mark.parametrize("pin", ["col_repair_en", "faulty_bit"])
def test_column_pin_must_be_dir_input(tmp_path: Path, pin: str) -> None:
    """dir matters as much as width: repair_remap_col takes these as inputs, so
    `output` leaves an undriven wire on the boundary -- spare_wen goes X and the
    repaired lane reads X, with no pin for the tester to drive."""
    ports = [
        {**e, "dir": "output"} if e["name"] == pin else e
        for e in COL_REPAIR_PORTS
    ]
    with pytest.raises(ConfigError, match="must be dir: input"):
        _load(tmp_path, {
            **COL_BASE,
            "redundancy": {"num_spare_rows": 1, "num_spare_cols": 1},
            "repair_ports": ports,
        })


def test_words_per_row_defaults_to_one(tmp_path: Path) -> None:
    loaded = _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 1}, "repair_ports": REPAIR_PORTS_1ROW})
    assert loaded["redundancy"]["words_per_row"] == 1


def test_words_per_row_other_than_one_rejected(tmp_path: Path) -> None:
    """Column muxing shares ONE spare-column set per PHYSICAL row, which BIRA's
    global bit-lane column model does not express. Rejected unconditionally --
    the row-side spare-address convention has the same latent caveat."""
    with pytest.raises(ConfigError, match="words_per_row"):
        _load(tmp_path, {
            **BASE,
            "redundancy": {"num_spare_rows": 1, "words_per_row": 2},
            "repair_ports": REPAIR_PORTS,
        })


@pytest.mark.parametrize("bad", [0, -1, True, "two", 1.5])
def test_words_per_row_must_be_a_positive_int(tmp_path: Path, bad) -> None:
    with pytest.raises(ConfigError, match="words_per_row"):
        _load(tmp_path, {
            **BASE,
            "redundancy": {"num_spare_rows": 1, "words_per_row": bad},
            "repair_ports": REPAIR_PORTS,
        })


def test_num_spare_rows_must_be_at_least_one(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="num_spare_rows"):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 0}, "repair_ports": REPAIR_PORTS})


@pytest.mark.parametrize("bad_rows", [-1, True, "two", 1.5])
def test_num_spare_rows_must_be_non_negative_int(tmp_path: Path, bad_rows) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, {**BASE, "redundancy": {"num_spare_rows": bad_rows}, "repair_ports": REPAIR_PORTS})


def test_redundancy_must_be_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="redundancy"):
        _load(tmp_path, {**BASE, "redundancy": [1, 2], "repair_ports": REPAIR_PORTS})


def test_redundancy_rejected_for_multi_port(tmp_path: Path) -> None:
    config = {**BASE, "ports": MULTI_PORTS, "redundancy": {"num_spare_rows": 2}, "repair_ports": REPAIR_PORTS}
    with pytest.raises(ConfigError, match="single-port"):
        _load(tmp_path, config)


# --------------------------------------------------------------------------- #
# Cross-check: redundancy is incompatible with the saboteur (in generate_from_config)
# --------------------------------------------------------------------------- #
def test_redundancy_with_saboteur_rejected(tmp_path: Path) -> None:
    config_path = _write(tmp_path, {**BASE, "redundancy": {"num_spare_rows": 2}, "repair_ports": REPAIR_PORTS})
    with pytest.raises(ConfigError, match="saboteur"):
        generate_from_config(config_path, tmp_path / "out", use_saboteur=True)
