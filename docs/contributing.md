# Contributor Guide: Tax Preparation Plugin

This guide provides instructions for developers who want to maintain the plugin, add new features, or update tax data for future years.

## 🛠️ Project Architecture
- **`/scripts`**: Python logic for tax calculations.
- **`scripts/tax_constants.json`**: Centralized source of truth for all IRS tax brackets, deductions, and limits.
- **`scripts/utils.py`**: Shared utility for loading constants and resolving paths.
- **`/references`**: Markdown files used by the LLM agent as domain knowledge.
- **`/docs`**: User and developer documentation.

## 📅 Updating for 2026 Tax Year
Updating for a new tax year is a centralized, two-step process:

1. **Update Constants**: Edit `scripts/tax_constants.json` with the new IRS inflation-adjusted figures (typically released by the IRS in Rev. Proc. 2025-XX late in the year).
2. **Synchronize Reference Docs**: Run the update script to automatically update the markdown references:
   ```bash
   python scripts/update_tax_constants.py --file scripts/tax_constants.json
   ```
3. **Verify**: Ensure that `references/tax_brackets_deductions.md` reflects the new year and values.

## 🗺️ Adding State-Specific Tax Logic
The plugin currently focuses on Federal taxes. To add a new state:

1. **Add State Constants**: Create a new key in `scripts/tax_constants.json` under `state_tax_rates` or create a new `states/` directory for complex multi-bracket states (like CA or NY).
2. **Implement Logic**: Create a new script `scripts/state_tax_calculator.py` that imports `utils.py`.
3. **Reference Docs**: Create `references/state_ca_guide.md` (example) to provide the LLM agent with state-specific context.

## ✅ Writing Tests
Accuracy is critical. All calculation logic must have corresponding tests.
- Add new test cases to the `tests/` directory (use `pytest`).
- For bug fixes, provide a test case that reproduces the error before applying the fix.

## 📜 Coding Standards
- **Standard Library First**: Prefer Python's standard library (json, csv, math, decimal) to minimize external dependencies.
- **Decimal for Currency**: Always use the `decimal` module for currency calculations to avoid floating-point errors.
- **Verbose Output**: Scripts should output clear, human-readable summaries that an LLM can easily parse.
- **License**: All contributions must adhere to the MIT License.

## 🚀 How to Contribute
1. **Fork** the repository.
2. **Create a branch** for your feature or bug fix (`git checkout -b feature/new-state-tax`).
3. **Commit** your changes and include tests.
4. **Submit a Pull Request** with a detailed description of your changes.
