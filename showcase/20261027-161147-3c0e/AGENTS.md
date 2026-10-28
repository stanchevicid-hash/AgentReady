# AGENTS.md

## Project layout
- Source package: `src/parcelpost/`
  - `api.py` — public API
  - `core/discounts.py` — discount logic
  - `pricing/tariff.py` — pricing rules
  - `pricing/zones.py` — zone definitions
- Tests: `tests/`
  - `test_api.py`, `test_core.py`, `test_pricing.py`

## Running tests
- **Windows (cmd.exe):**
  ```
  set PYTHONPATH=src&& python -m unittest discover -s tests
  ```
- **POSIX (bash/zsh):**
  ```
  PYTHONPATH=src python -m unittest discover -s tests
  ```
- **Single test file (Windows):**
  ```
  set PYTHONPATH=src&& python -m unittest tests.test_api
  ```
- **Single test method (Windows):**
  ```
  set PYTHONPATH=src&& python -m unittest tests.test_api.TestAPI.test_calculate
  ```

## Working rules
- Always set `PYTHONPATH=src` before running any test command
- Use `unittest discover` — **not** `pytest`
- Run the full suite after every change to verify
- No external dependencies; stdlib only

## Known pitfalls
- **README is outdated:**
  - Documents `pytest` instead of `unittest discover`
  - References `parcelpost/rates.py` and `rules.py` — these do not exist
  - Actual modules: `parcelpost.pricing.tariff`, `parcelpost.pricing.zones`, `parcelpost.core.discounts`
- **No `pyproject.toml` or `setup.py`** — editable install (`pip install -e .`) not possible; manual `PYTHONPATH=src` required
- **No prior agent instructions** — every agent must rediscover test setup from scratch
