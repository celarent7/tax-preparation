# Tax Preparation Plugin (2025 Tax Year)

A comprehensive suite of Python scripts and markdown-based reference guides designed for US tax analysis and preparation, optimized for use with Large Language Models (LLMs) like Claude.

## 🚀 High-Level Value Proposition
This plugin empowers individuals and families to take control of their US tax preparation. It specializes in complex scenarios like **stock-based compensation (RSUs, ESPPs)**, **self-employment optimization**, and **proactive deduction discovery**, ensuring you don't leave money on the table while maintaining audit-ready records.

---

## ✨ Key Features

### 📈 Investment & Stock Compensation
- **RSU/ESPP Calculator**: Accurately calculate cost basis and identify common 1099-B errors.
- **Form 8949 Support**: Generate data for capital gains reporting with appropriate adjustment codes.
- **Vesting Analysis**: Track vesting events and withholding shortfalls.

### 💼 Self-Employment & Small Business
- **SE Tax Calculator**: Compute self-employment taxes and half-SE tax adjustments.
- **Estimated Payments**: Calculate quarterly obligations to avoid IRS underpayment penalties.
- **Business Deductions**: Guided identification of eligible business expenses (home office, mileage, equipment).

### 🔍 Deduction & Credit Optimization
- **Standard vs. Itemized**: Automatically compare the most beneficial filing strategy.
- **Deduction Finder**: Proactively probes for commonly missed deductions (medical, SALT, mortgage interest).
- **Credit Eligibility**: Analyzes qualification for CTC, AOTC, EITC, and more.

### 🛡️ Quality & Verification
- **Draft Reviewer**: Cross-check your draft tax return (PDF or manual) against computed data to catch errors before you file.
- **FBAR Workflow**: Step-by-step guidance for tracking and reporting foreign bank accounts (FinCEN Form 114).

---

## 🏗️ Architecture Overview

The plugin is structured for both direct execution and as a toolset for AI agents:

1.  **`/scripts`**: Python-based core logic for all tax calculations and data processing.
2.  **`/references`**: Markdown knowledge base containing IRS rules, tax brackets, and extraction templates.
3.  **`/skills`**: Orchestration logic (e.g., `SKILL.md`) that allows LLMs to understand when and how to invoke the toolset.
4.  **`/examples`**: Anonymized sample data for testing and validation.

---

## 🛠️ Installation

### Prerequisites
- Python 3.9+
- An LLM client supporting MCP (Model Context Protocol), such as [Claude for Desktop](https://claude.ai/download).

### Local Setup
1.  Clone the repository:
    ```bash
    git clone https://github.com/celarent7/tax-preparation.git
    cd tax-preparation
    ```
2.  Install dependencies:
    ```bash
    pip install pandas pdfplumber pytest
    ```

### MCP Integration (for Claude)
Add the following to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "tax-preparation": {
      "command": "python",
      "args": ["path/to/tax-preparation/scripts/mcp_server.py"]
    }
  }
}
```
*(See `mcp_config.json` for the full configuration snippet.)*

---

## 💬 Example Queries
You can use the following queries with an AI agent equipped with this plugin:
- *"I just received my RSU vesting statement. Can you help me calculate if enough tax was withheld?"*
- *"Based on my W-2 and 1099-NEC, what are my estimated quarterly payments for 2025?"*
- *"Review my draft Form 1040 (attached) and tell me if the RSU cost basis looks correct."*
- *"Am I eligible for the Child Tax Credit if my AGI is $X and I have Y dependents?"*

---

## 🔒 Privacy & Security
- **Local Processing**: All tax calculations and document analysis are performed **locally on your machine**.
- **No Persistence**: This tool does not store, transmit, or upload your tax documents or PII to any external server (other than the Large Language Model provider you explicitly use to interface with this plugin).
- **Data Isolation**: Your sensitive financial data stays within your local environment.
- **PII Protection**: We recommend using the anonymized data in `examples/` for testing; always be cautious when providing PII to any LLM.

---

## 📄 License & Disclaimer
- **License**: This project is licensed under the [MIT License](LICENSE).
- **Disclaimer**: This tool is for **educational purposes only** and is NOT professional tax advice. Please see [DISCLAIMER.md](DISCLAIMER.md) for full legal details.
- **Scope & Limitations**: See [limitations.md](docs/limitations.md) for a list of unsupported tax scenarios.

---
*Created by Celarent. Optimized for the 2025 Tax Year.*

## 📜 Credits & Provenance
This project is an independent fork and contains code originally developed by [mrelph](https://github.com/mrelph) in the [claude-agents-skills](https://github.com/mrelph/claude-agents-skills) repository. It is licensed under the MIT License, which allows for modification and redistribution with proper attribution.
