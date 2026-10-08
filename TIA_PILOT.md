# Test Impact Analysis (TIA) Pilot — pytest-tia

## Overview

This project uses **pytest-tia** as a pilot for Test Impact Analysis. The goal is to enable faster feedback loops by running only tests affected by recent code changes.

## Why pytest-tia over pytest-testmon

pytest-testmon has compatibility issues with the current environment (TypeError on metadata access). pytest-tia uses a different approach - it records coverage maps using git diff analysis and is more robust in our setup.

## Configuration

### pytest.ini

TIA is configured as opt-in. It is NOT enabled by default in the standard test run.

```ini
[pytest]
# pytest-tia: Test Impact Analysis (TIA) pilot - opt-in via `tia record` / `tia run`
```

### .tia/map.json

The `.tia/map.json` file stores the test-to-code dependency map. This file should be committed to the repo to share the map across CI runs.

## Usage

### 1. Record Initial Coverage Map (Run Once)

```bash
# Build the initial dependency database - run all tests with tia record
tia record

# Or record from a specific test subset
tia record tests/unit
```

This creates `.tia/map.json` containing the test-to-code dependency map, stamped with the current git ref.

### 2. Run Affected Tests Only (CI/Development)

```bash
# After making code changes, run only affected tests
tia run

# This will:
# 1. Diff against the recorded git ref
# 2. Parse files as they existed at that ref (git show)
# 3. Resolve changed lines to changed functions
# 4. Select tests by three rules:
#    - Function hit: tests executing functions whose bodies changed
#    - Module-level fallback: runs all tests touching files with module-level modifications
#    - Data dependency: captures non-Python file reads via sys.addaudithook
#    - New test detection: always runs unmeasured tests
# 5. Run selected tests via pytest
```

### 3. CI Integration

For CI, we recommend a two-stage approach:

```yaml
# Stage 1: Full test run + record map (nightly or on merge to main)
- name: Full test suite + TIA record (nightly)
  if: github.event_name == 'schedule' || github.ref == 'refs/heads/main'
  run: tia record

# Stage 2: TIA-optimized run (PRs)
- name: TIA-optimized tests (PRs)
  if: github.event_name == 'pull_request'
  run: tia run
```

### 4. Manual Commands

```bash
# Record coverage map
tia record [PATH]

# Run affected tests
tia run [PATH]

# Diff against another ref (e.g., main)
tia run --since main

# Show selection without running
tia run --list

# Print Markdown summary
tia run --report -

# Summarize recorded map
tia status

# Run bundled map store (for sharing maps across CI)
tia serve --dir ./maps

# Publish map
tia push --to <dir|url>

# Fetch published map
tia pull --from <dir|url>
```

## Workflow

### For Developers

1. **First time setup**: Run `tia record` once to build the database
2. **During development**: Run `tia run` after making changes - only affected tests run
3. **Before pushing**: Run full test suite without tia: `pytest` (or let CI handle it)

### For CI

1. **Nightly/On merge**: `tia record` builds/updates the `.tia/map.json`
2. **PRs**: `tia run` runs only affected tests for fast feedback
3. **Fallback**: If tia selects too many tests, run full suite

## Important Notes

### TIA Limitations

- TIA does NOT track: static files (except via data dependency audit hook), external services
- Changing a method signature often causes TIA to select all tests in that module (by design)
- TIA exposes existing test dependencies - if tests fail under TIA, fix the test dependencies

### Best Practices

1. **Commit `.tia/map.json`** to share the dependency map across the team
2. **Run full suite periodically** (nightly) to keep the map accurate
3. **Don't gate PRs on TIA yet** - this is a pilot, not a gate
4. **Monitor selection ratio** - if TIA selects >80% of tests consistently, investigate

### Troubleshooting

- **Too many tests selected**: Usually by design when changing shared utilities. Consider splitting utilities or accepting full runs for those changes.
- **Tests fail under TIA**: TIA likely exposed a hidden test dependency. Fix the test, not TIA.
- **Map corruption**: Delete `.tia/map.json` and rebuild with `tia record`

## Integration with Quarantine

The quarantine mechanism (pytest-rerunfailures) works independently of TIA. Quarantined tests that are selected by TIA will still be rerun according to their flaky configuration.

## References

- pytest-tia: https://github.com/breadMSA/pytest-tia
- PyPI: https://pypi.org/project/pytest-tia/
- Requirements: Python >=3.10, pytest >=7, coverage >=7