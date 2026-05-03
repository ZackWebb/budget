# YNAB Budget Data Exporter for LLM Analysis

Export your YNAB budget data in formats suitable for LLM analysis.

## Setup

1. Install dependencies:
   ```bash
   pip install -e .
   ```

2. Get your YNAB Personal Access Token:
   - Go to YNAB web app → Account Settings → Developer Settings
   - Create a new Personal Access Token
   - Set the environment variable:
     ```bash
     export YNAB_ACCESS_TOKEN="your-token-here"
     ```

## Usage

```bash
# Export last-used budget (6 months of data)
python main.py

# Export 12 months of data
python main.py --months 12

# List all available budgets
python main.py --list-budgets

# Export a specific budget
python main.py --budget-id <budget-id>

# Save to custom directory
python main.py --output ./exports

# Output only JSON or Markdown
python main.py --json-only
python main.py --markdown-only
```

## Output Files

Each export creates two files:
- `ynab_export_YYYYMMDD_HHMMSS.json` - Complete structured data
- `ynab_export_YYYYMMDD_HHMMSS.md` - Markdown summary for LLM chat

## Data Exported

- **Budget metadata**: Accounts, category groups, categories
- **Monthly budgets**: Allocations vs actual spending by category
- **Category goals**: Type, target amount, deadline, percentage complete
- **Transactions**: Configurable date range
- **Spending patterns**: Over/under budget analysis by category
