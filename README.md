# autombist

autombist packages the MBIST generator as an installable Python project with a single CLI entry point.

## Installation

Install into your active environment from the repository root:

```bash
python -m pip install .
```

For editable development installs:

```bash
python -m pip install -e .
```

## Usage

Generate the default wrapper artifacts:

```bash
autombist --config config.yml --out out
```

Enable saboteur fault generation and emit the module-local fault simulation Makefile:

```bash
autombist --config config.yml --out out --test --faults 50 --seed 1234
```

Inspect the available options with:

```bash
autombist --help
```