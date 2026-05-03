"""YNAB Budget Data Exporter for LLM Analysis.

This script pulls budget data from YNAB's API and outputs it in formats
suitable for feeding to an LLM for financial analysis.
"""

import argparse
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path

import ynab
from ynab.api import budgets_api, categories_api, transactions_api, months_api, accounts_api
from ynab.exceptions import ApiException


DEFAULT_BUDGET_ID = "187b9025-51da-496d-9171-5f6b249e82a7"  # Abby's & Zack's Budget


def get_token_from_pass(name: str = "budget/YNAB_ACCESS_TOKEN") -> str | None:
    """Retrieve the YNAB token from the `pass` password store."""
    try:
        result = subprocess.run(
            ["pass", name], capture_output=True, text=True, check=True
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def milliunits_to_currency(milliunits: int | None) -> float:
    """Convert YNAB milliunits to standard currency format."""
    if milliunits is None:
        return 0.0
    return milliunits / 1000.0


@dataclass
class CategoryGoal:
    """Represents a YNAB category goal."""
    type: str | None
    target_amount: float
    target_month: str | None
    percentage_complete: int
    months_to_budget: int | None
    under_funded: float


@dataclass
class Category:
    """Represents a YNAB category with budget data."""
    id: str
    name: str
    group_name: str
    budgeted: float
    activity: float
    balance: float
    goal: CategoryGoal | None
    hidden: bool
    deleted: bool


@dataclass
class Account:
    """Represents a YNAB account."""
    id: str
    name: str
    type: str
    on_budget: bool
    closed: bool
    balance: float
    cleared_balance: float
    uncleared_balance: float


@dataclass
class Transaction:
    """Represents a YNAB transaction."""
    id: str
    date: str
    amount: float
    payee_name: str | None
    category_name: str | None
    memo: str | None
    cleared: str
    approved: bool
    account_name: str
    transfer_account_name: str | None


@dataclass
class MonthlyBudget:
    """Represents budget data for a specific month."""
    month: str
    income: float
    budgeted: float
    activity: float
    to_be_budgeted: float
    categories: list[dict]


@dataclass
class SpendingPattern:
    """Represents spending analysis for a category."""
    category_name: str
    group_name: str
    total_budgeted: float
    total_spent: float
    difference: float
    status: str  # "over", "under", or "on_track"
    months_over: int
    months_under: int
    average_monthly_spend: float


@dataclass
class BudgetExport:
    """Complete budget export data structure."""
    export_timestamp: str
    date_range: dict
    budget_name: str
    budget_id: str
    currency_format: dict
    accounts: list[Account]
    category_groups: list[dict]
    categories: list[Category]
    monthly_budgets: list[MonthlyBudget]
    transactions: list[Transaction]
    spending_patterns: list[SpendingPattern]


class YNABExporter:
    """Exports YNAB budget data for LLM analysis."""

    def __init__(self, access_token: str | None = None):
        """Initialize the exporter with YNAB API credentials."""
        token = access_token or get_token_from_pass()
        if not token:
            raise ValueError(
                "YNAB access token is required. Pass --token or store it in "
                "`pass` at budget/YNAB_ACCESS_TOKEN."
            )

        configuration = ynab.Configuration(access_token=token)
        self.api_client = ynab.ApiClient(configuration)
        self.budgets_api = budgets_api.BudgetsApi(self.api_client)
        self.categories_api = categories_api.CategoriesApi(self.api_client)
        self.transactions_api = transactions_api.TransactionsApi(self.api_client)
        self.months_api = months_api.MonthsApi(self.api_client)
        self.accounts_api = accounts_api.AccountsApi(self.api_client)

    def list_budgets(self, include_archived: bool = False) -> list[dict]:
        """List available budgets, hiding archived ones by default."""
        response = self.budgets_api.get_budgets()
        return [
            {"id": b.id, "name": b.name, "last_modified": str(b.last_modified_on)}
            for b in response.data.budgets
            if include_archived or "archived" not in b.name.lower()
        ]

    def get_budget(self, budget_id: str = "last-used") -> dict:
        """Get full budget details."""
        response = self.budgets_api.get_budget_by_id(budget_id)
        return response.data.budget

    def get_accounts(self, budget_id: str = "last-used") -> list[Account]:
        """Get all accounts for a budget."""
        response = self.accounts_api.get_accounts(budget_id)
        return [
            Account(
                id=a.id,
                name=a.name,
                type=a.type.value if hasattr(a.type, 'value') else (a.type or "unknown"),
                on_budget=a.on_budget,
                closed=a.closed,
                balance=milliunits_to_currency(a.balance),
                cleared_balance=milliunits_to_currency(a.cleared_balance),
                uncleared_balance=milliunits_to_currency(a.uncleared_balance),
            )
            for a in response.data.accounts
            if not a.deleted
        ]

    def get_categories(self, budget_id: str = "last-used") -> tuple[list[dict], list[Category]]:
        """Get all category groups and categories with goal info."""
        response = self.categories_api.get_categories(budget_id)

        category_groups = []
        categories = []

        for group in response.data.category_groups:
            if group.deleted or group.hidden:
                continue

            category_groups.append({
                "id": group.id,
                "name": group.name,
            })

            for cat in group.categories:
                if cat.deleted:
                    continue

                goal = None
                if cat.goal_type:
                    goal_type = cat.goal_type.value if hasattr(cat.goal_type, 'value') else cat.goal_type
                    goal = CategoryGoal(
                        type=goal_type,
                        target_amount=milliunits_to_currency(cat.goal_target),
                        target_month=str(cat.goal_target_month) if cat.goal_target_month else None,
                        percentage_complete=cat.goal_percentage_complete or 0,
                        months_to_budget=cat.goal_months_to_budget,
                        under_funded=milliunits_to_currency(cat.goal_under_funded),
                    )

                categories.append(Category(
                    id=cat.id,
                    name=cat.name,
                    group_name=group.name,
                    budgeted=milliunits_to_currency(cat.budgeted),
                    activity=milliunits_to_currency(cat.activity),
                    balance=milliunits_to_currency(cat.balance),
                    goal=goal,
                    hidden=cat.hidden,
                    deleted=cat.deleted,
                ))

        return category_groups, categories

    def get_monthly_budgets(
        self, budget_id: str = "last-used", months: int = 6
    ) -> list[MonthlyBudget]:
        """Get monthly budget data for the past N months."""
        monthly_budgets = []
        today = datetime.now()

        for i in range(months):
            target_date = today - relativedelta(months=i)
            month_str = target_date.strftime("%Y-%m-01")

            try:
                response = self.months_api.get_budget_month(budget_id, month_str)
                month_data = response.data.month

                categories = []
                for cat in month_data.categories:
                    if cat.deleted or cat.hidden:
                        continue
                    categories.append({
                        "name": cat.name,
                        "group_name": cat.category_group_name,
                        "budgeted": milliunits_to_currency(cat.budgeted),
                        "activity": milliunits_to_currency(cat.activity),
                        "balance": milliunits_to_currency(cat.balance),
                        "goal_target": milliunits_to_currency(cat.goal_target),
                        "goal_type": cat.goal_type.value if hasattr(cat.goal_type, 'value') else cat.goal_type,
                        "goal_cadence": cat.goal_cadence.value if hasattr(cat.goal_cadence, 'value') else cat.goal_cadence,
                    })

                monthly_budgets.append(MonthlyBudget(
                    month=month_str,
                    income=milliunits_to_currency(month_data.income),
                    budgeted=milliunits_to_currency(month_data.budgeted),
                    activity=milliunits_to_currency(month_data.activity),
                    to_be_budgeted=milliunits_to_currency(month_data.to_be_budgeted),
                    categories=categories,
                ))
            except ApiException as e:
                if e.status == 404:
                    continue
                raise

        return monthly_budgets

    def get_transactions(
        self, budget_id: str = "last-used", months: int = 6
    ) -> list[Transaction]:
        """Get transactions for the past N months."""
        since_date = (datetime.now() - relativedelta(months=months)).strftime("%Y-%m-%d")

        response = self.transactions_api.get_transactions(
            budget_id, since_date=since_date
        )

        return [
            Transaction(
                id=t.id,
                date=str(t.var_date),
                amount=milliunits_to_currency(t.amount),
                payee_name=t.payee_name,
                category_name=t.category_name,
                memo=t.memo,
                cleared=t.cleared.value if hasattr(t.cleared, 'value') else (t.cleared or "unknown"),
                approved=t.approved,
                account_name=t.account_name,
                transfer_account_name=t.transfer_account_id,
            )
            for t in response.data.transactions
            if not t.deleted
        ]

    def analyze_spending_patterns(
        self, monthly_budgets: list[MonthlyBudget]
    ) -> list[SpendingPattern]:
        """Analyze over/under spending patterns by category."""
        category_data: dict[str, dict] = {}

        for month in monthly_budgets:
            for cat in month.categories:
                name = cat["name"]
                if name not in category_data:
                    category_data[name] = {
                        "total_budgeted": 0,
                        "total_spent": 0,
                        "months_over": 0,
                        "months_under": 0,
                        "month_count": 0,
                    }

                budgeted = cat["budgeted"]
                spent = abs(cat["activity"])

                category_data[name]["total_budgeted"] += budgeted
                category_data[name]["total_spent"] += spent
                category_data[name]["month_count"] += 1

                if spent > budgeted and budgeted > 0:
                    category_data[name]["months_over"] += 1
                elif spent < budgeted * 0.9 and budgeted > 0:
                    category_data[name]["months_under"] += 1

        patterns = []
        for name, data in category_data.items():
            if data["total_budgeted"] == 0 and data["total_spent"] == 0:
                continue

            difference = data["total_budgeted"] - data["total_spent"]
            if difference < -10:
                status = "over"
            elif difference > data["total_budgeted"] * 0.1:
                status = "under"
            else:
                status = "on_track"

            patterns.append(SpendingPattern(
                category_name=name,
                group_name="",
                total_budgeted=data["total_budgeted"],
                total_spent=data["total_spent"],
                difference=difference,
                status=status,
                months_over=data["months_over"],
                months_under=data["months_under"],
                average_monthly_spend=data["total_spent"] / data["month_count"] if data["month_count"] > 0 else 0,
            ))

        patterns.sort(key=lambda p: p.difference)
        return patterns

    def export(
        self, budget_id: str = "last-used", months: int = 6
    ) -> BudgetExport:
        """Export complete budget data."""
        budget = self.get_budget(budget_id)
        accounts = self.get_accounts(budget_id)
        category_groups, categories = self.get_categories(budget_id)
        monthly_budgets = self.get_monthly_budgets(budget_id, months)
        transactions = self.get_transactions(budget_id, months)
        spending_patterns = self.analyze_spending_patterns(monthly_budgets)

        today = datetime.now()
        since_date = (today - relativedelta(months=months)).strftime("%Y-%m-%d")

        currency_format = {}
        if budget.currency_format:
            currency_format = {
                "iso_code": budget.currency_format.iso_code,
                "symbol": budget.currency_format.currency_symbol,
                "decimal_places": budget.currency_format.decimal_digits,
            }

        return BudgetExport(
            export_timestamp=today.isoformat(),
            date_range={
                "start": since_date,
                "end": today.strftime("%Y-%m-%d"),
                "months": months,
            },
            budget_name=budget.name,
            budget_id=budget.id,
            currency_format=currency_format,
            accounts=accounts,
            category_groups=category_groups,
            categories=categories,
            monthly_budgets=monthly_budgets,
            transactions=transactions,
            spending_patterns=spending_patterns,
        )


def dataclass_to_dict(obj) -> dict | list | str | int | float | bool | None:
    """Convert dataclass instances to dictionaries recursively."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: dataclass_to_dict(v) for k, v in obj.__dict__.items()}
    elif isinstance(obj, list):
        return [dataclass_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def export_to_json(export: BudgetExport, output_path: Path) -> None:
    """Export budget data to JSON file."""
    data = dataclass_to_dict(export)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_transfer_transaction(t: Transaction) -> bool:
    """Check if a transaction is an account-to-account transfer."""
    return t.payee_name and "Transfer" in t.payee_name


def get_filtered_transactions(transactions: list[Transaction]) -> list[Transaction]:
    """Filter out transfer transactions from Uncategorized category."""
    return [
        t for t in transactions
        if not (t.category_name == "Uncategorized" and is_transfer_transaction(t))
    ]


def export_to_markdown(export: BudgetExport, output_path: Path) -> None:
    """Export budget data to Markdown summary suitable for LLM analysis."""
    lines = []

    # Filter out transfers from Uncategorized for analysis
    filtered_transactions = get_filtered_transactions(export.transactions)

    lines.append(f"# YNAB Budget Export: {export.budget_name}")
    lines.append("")
    lines.append(f"**Export Date:** {export.export_timestamp}")
    lines.append(f"**Date Range:** {export.date_range['start']} to {export.date_range['end']} ({export.date_range['months']} months)")
    if export.currency_format:
        lines.append(f"**Currency:** {export.currency_format.get('iso_code', 'USD')} ({export.currency_format.get('symbol', '$')})")
    lines.append("")

    lines.append("## Accounts Summary")
    lines.append("")
    lines.append("| Account | Type | Balance | On Budget |")
    lines.append("|---------|------|---------|-----------|")
    total_balance = 0
    for acc in export.accounts:
        if not acc.closed:
            lines.append(f"| {acc.name} | {acc.type} | ${acc.balance:,.2f} | {'Yes' if acc.on_budget else 'No'} |")
            if acc.on_budget:
                total_balance += acc.balance
    lines.append(f"| **Total (On Budget)** | | **${total_balance:,.2f}** | |")
    lines.append("")

    lines.append("## Monthly Budget Overview")
    lines.append("")
    lines.append("| Month | Income | Budgeted | Spent | To Be Budgeted |")
    lines.append("|-------|--------|----------|-------|----------------|")
    for month in sorted(export.monthly_budgets, key=lambda m: m.month, reverse=True):
        spent = abs(month.activity)
        lines.append(f"| {month.month[:7]} | ${month.income:,.2f} | ${month.budgeted:,.2f} | ${spent:,.2f} | ${month.to_be_budgeted:,.2f} |")
    lines.append("")

    lines.append("## Category Goals Progress")
    lines.append("")
    goals = [c for c in export.categories if c.goal and not c.hidden]
    if goals:
        lines.append("| Category | Group | Goal Type | Target | Progress | Under-funded |")
        lines.append("|----------|-------|-----------|--------|----------|--------------|")
        for cat in sorted(goals, key=lambda c: c.goal.percentage_complete if c.goal else 0):
            goal = cat.goal
            target = f"${goal.target_amount:,.2f}" if goal.target_amount else "N/A"
            if goal.target_month:
                target += f" by {goal.target_month[:7]}"
            lines.append(f"| {cat.name} | {cat.group_name} | {goal.type} | {target} | {goal.percentage_complete}% | ${goal.under_funded:,.2f} |")
    else:
        lines.append("*No category goals configured.*")
    lines.append("")

    lines.append("## Spending Patterns Analysis")
    lines.append("")
    lines.append("### Categories Over Budget")
    lines.append("")
    over_budget = [p for p in export.spending_patterns if p.status == "over"]
    if over_budget:
        lines.append("| Category | Budgeted | Spent | Over By | Months Over |")
        lines.append("|----------|----------|-------|---------|-------------|")
        for p in over_budget[:10]:
            over_by = abs(p.difference)
            lines.append(f"| {p.category_name} | ${p.total_budgeted:,.2f} | ${p.total_spent:,.2f} | ${over_by:,.2f} | {p.months_over} |")
    else:
        lines.append("*No categories significantly over budget.*")
    lines.append("")

    lines.append("### Categories Under Budget")
    lines.append("")
    under_budget = [p for p in export.spending_patterns if p.status == "under"]
    if under_budget:
        lines.append("| Category | Budgeted | Spent | Under By | Avg Monthly |")
        lines.append("|----------|----------|-------|----------|-------------|")
        for p in sorted(under_budget, key=lambda x: x.difference, reverse=True)[:10]:
            lines.append(f"| {p.category_name} | ${p.total_budgeted:,.2f} | ${p.total_spent:,.2f} | ${p.difference:,.2f} | ${p.average_monthly_spend:,.2f} |")
    else:
        lines.append("*No categories significantly under budget.*")
    lines.append("")

    # Month-by-month category analysis
    lines.append("## Monthly Category Breakdown (Target vs Assigned vs Spent)")
    lines.append("")
    lines.append("This section shows each category's monthly target (goal), amount assigned (budgeted), and amount spent (activity).")
    lines.append("Use this to identify patterns like consistently needing to assign more than the target to cover overspending.")
    lines.append("")

    # Collect all categories with goals and organize by category
    category_monthly_data: dict[str, list[dict]] = {}
    for month in sorted(export.monthly_budgets, key=lambda m: m.month):
        for cat in month.categories:
            if cat.get("goal_target") or cat.get("budgeted") or cat.get("activity"):
                cat_name = cat["name"]
                if cat_name not in category_monthly_data:
                    category_monthly_data[cat_name] = []
                category_monthly_data[cat_name].append({
                    "month": month.month[:7],
                    "group": cat.get("group_name", ""),
                    "target": cat.get("goal_target", 0) or 0,
                    "assigned": cat.get("budgeted", 0) or 0,
                    "spent": abs(cat.get("activity", 0) or 0),
                    "goal_type": cat.get("goal_type"),
                    "goal_cadence": cat.get("goal_cadence"),
                })

    # Filter to categories that have goals (monthly targets) and show interesting patterns
    categories_with_goals = {
        name: data for name, data in category_monthly_data.items()
        if any(m["target"] > 0 for m in data)
    }

    # Sort by categories that have the most overspending incidents
    def overspend_score(cat_data: list[dict]) -> float:
        return sum(
            max(0, m["spent"] - m["target"])
            for m in cat_data if m["target"] > 0
        )

    sorted_categories = sorted(
        categories_with_goals.items(),
        key=lambda x: overspend_score(x[1]),
        reverse=True
    )

    for cat_name, months_data in sorted_categories[:20]:  # Top 20 categories
        group_name = months_data[0].get("group", "") if months_data else ""
        lines.append(f"### {cat_name}")
        if group_name:
            lines.append(f"*Group: {group_name}*")
        lines.append("")
        lines.append("| Month | Target | Assigned | Spent | Assigned vs Target | Spent vs Target |")
        lines.append("|-------|--------|----------|-------|-------------------|-----------------|")

        for m in months_data:
            target = m["target"]
            assigned = m["assigned"]
            spent = m["spent"]

            # Calculate differences
            if target > 0:
                assigned_diff = assigned - target
                spent_diff = spent - target
                assigned_vs = f"+${assigned_diff:,.2f}" if assigned_diff > 0 else f"-${abs(assigned_diff):,.2f}" if assigned_diff < 0 else "on target"
                spent_vs = f"+${spent_diff:,.2f}" if spent_diff > 0 else f"-${abs(spent_diff):,.2f}" if spent_diff < 0 else "on target"
            else:
                assigned_vs = "no target"
                spent_vs = "no target"

            lines.append(f"| {m['month']} | ${target:,.2f} | ${assigned:,.2f} | ${spent:,.2f} | {assigned_vs} | {spent_vs} |")

        lines.append("")

    lines.append("## Recent Transactions Summary")
    lines.append("")
    lines.append(f"Total transactions in period: {len(filtered_transactions)}")
    lines.append("")

    category_totals: dict[str, float] = {}
    for t in filtered_transactions:
        if t.category_name and t.amount < 0:
            cat = t.category_name
            category_totals[cat] = category_totals.get(cat, 0) + abs(t.amount)

    # Remove Uncategorized if it has no spending after filtering transfers
    if "Uncategorized" in category_totals and category_totals["Uncategorized"] == 0:
        del category_totals["Uncategorized"]

    if category_totals:
        lines.append("### Top Spending Categories (by transaction volume)")
        lines.append("")
        lines.append("| Category | Total Spent |")
        lines.append("|----------|-------------|")
        for cat, total in sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:15]:
            lines.append(f"| {cat} | ${total:,.2f} |")
    lines.append("")

    # Check if Uncategorized has any non-transfer transactions
    uncategorized_has_real_transactions = any(
        t for t in filtered_transactions if t.category_name == "Uncategorized"
    )

    lines.append("## Current Category Balances")
    lines.append("")
    lines.append("| Category | Group | Budgeted | Activity | Balance |")
    lines.append("|----------|-------|----------|----------|---------|")
    for cat in sorted(export.categories, key=lambda c: (c.group_name, c.name)):
        # Skip Uncategorized if it only has transfer transactions
        if cat.name == "Uncategorized" and not uncategorized_has_real_transactions:
            continue
        if not cat.hidden and (cat.budgeted != 0 or cat.activity != 0 or cat.balance != 0):
            lines.append(f"| {cat.name} | {cat.group_name} | ${cat.budgeted:,.2f} | ${cat.activity:,.2f} | ${cat.balance:,.2f} |")
    lines.append("")

    lines.append("---")
    lines.append("*This export was generated for LLM analysis. Use the accompanying JSON file for detailed programmatic access.*")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def cmd_pull(args, exporter):
    """Handle the pull subcommand."""
    print(f"Exporting budget data for the past {args.months} months...")

    try:
        export = exporter.export(args.budget_id, args.months)
    except ApiException as e:
        print(f"API Error: {e.status} - {e.reason}")
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"ynab_export_{timestamp}"

    if not args.markdown_only:
        json_path = args.output / f"{base_name}.json"
        export_to_json(export, json_path)
        print(f"JSON export saved to: {json_path}")

    if not args.json_only:
        md_path = args.output / f"{base_name}.md"
        export_to_markdown(export, md_path)
        print(f"Markdown export saved to: {md_path}")

    print(f"\nExport complete!")
    print(f"  Budget: {export.budget_name}")
    print(f"  Date range: {export.date_range['start']} to {export.date_range['end']}")
    print(f"  Accounts: {len(export.accounts)}")
    print(f"  Categories: {len(export.categories)}")
    print(f"  Transactions: {len(export.transactions)}")

    return 0


def cmd_budgets(args, exporter):
    """Handle the budgets subcommand."""
    print("Available budgets:")
    for budget in exporter.list_budgets(include_archived=args.all):
        print(f"  - {budget['name']} (ID: {budget['id']})")
        print(f"    Last modified: {budget['last_modified']}")
    return 0


def add_pull_args(parser):
    """Add pull/export arguments to a parser."""
    parser.add_argument(
        "--budget-id", "-b",
        default=DEFAULT_BUDGET_ID,
        help=f"Budget ID to export (default: {DEFAULT_BUDGET_ID})"
    )
    parser.add_argument(
        "--months", "-m",
        type=int,
        default=3,
        help="Number of months of history to export (default: 3)"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("exports"),
        help="Output directory for export files (default: exports/)"
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only output JSON, skip Markdown"
    )
    parser.add_argument(
        "--markdown-only",
        action="store_true",
        help="Only output Markdown, skip JSON"
    )


def main():
    """Main entry point for the YNAB Budget Exporter."""
    parser = argparse.ArgumentParser(
        description="YNAB budget tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--token", "-t",
        help="YNAB Personal Access Token (default: `pass budget/YNAB_ACCESS_TOKEN`)"
    )

    subparsers = parser.add_subparsers(dest="command")

    pull_parser = subparsers.add_parser("pull", help="Pull fresh budget data from YNAB")
    add_pull_args(pull_parser)

    budgets_parser = subparsers.add_parser("budgets", help="List all available budgets")
    budgets_parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Include archived budgets",
    )

    args = parser.parse_args()

    try:
        exporter = YNABExporter(access_token=args.token)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    if args.command == "pull":
        return cmd_pull(args, exporter)
    elif args.command == "budgets":
        return cmd_budgets(args, exporter)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    exit(main())
