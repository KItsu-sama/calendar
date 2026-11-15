# Shared output formatting

import json


def _print_json(value: dict) -> None:
    print(json.dumps(value, indent=2))


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("No results")
        return

    # Determine the columns to display
    columns = list(rows[0].keys())

    # Calculate the maximum width for each column
    column_widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            column_widths[col] = max(column_widths[col], len(str(row[col])))

    # Print the header
    header = " | ".join(f"{col:<{column_widths[col]}}" for col in columns)
    print(header)
    print("-" * len(header))

    # Print each row
    for row in rows:
        line = " | ".join(f"{str(row[col]):<{column_widths[col]}}" for col in columns)
        print(line)


def print_activity():
    ...


def print_error():
    ...

