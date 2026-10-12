#!/usr/bin/env python3
"""Check saved PICT arrays, feasible coverage, and the README's transfer oracle.

Uses only Python's standard library; does not call a transfer implementation.
Pass --repeat to rerun the five documented PICT commands and compare bytes.
"""

import argparse
import csv
import re
import subprocess
from collections import Counter
from decimal import Decimal
from itertools import combinations, product
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AMOUNTS = dict(zip(
    "A_NEG A_ZERO A_MIN A_L_BELOW A_L_EQ A_L_ABOVE A_B_BELOW A_B_EQ A_B_ABOVE".split(),
    map(Decimal, ("-1", "0", "0.01", "49.99", "50", "50.01", "99.99", "100", "100.01")),
))
REMAINING = {"L_FRESH": Decimal(200), "L_PARTIAL": Decimal(50), "L_EXHAUSTED": Decimal(0)}
JOBS = (
    ("browser.model", "browser-2way.txt", 2),
    ("browser.model", "browser-3way.txt", 3),
    ("transfer-nc.model", "transfer-nc.txt", 2),
    ("transfer.model", "transfer.txt", 2),
    ("transfer-neg.model", "transfer-neg.txt", 2),
)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def read_model(filename):
    """Read parameter declarations from these models, preserving column order."""
    params = {}
    for line in (ROOT / filename).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" in line and not line.startswith("IF "):
            name, values = line.split(":", 1)
            params[name.strip()] = tuple(value.strip() for value in values.split(","))
    return params


def clean(row):
    return tuple(value.removeprefix("~") for value in row)


def constraints(row):
    source, target, amount, currency, daily = clean(row)
    c1 = not (source == "F_MISSING" and target == "T_SAME")
    c2 = amount != "A_ZERO" or (source, target, currency, daily) == (
        "F_OK", "T_OTHER", "C_USD", "L_FRESH",
    )
    return c1, c2


def permitted(filename, row):
    if filename.startswith("browser") or filename == "transfer-nc.model":
        return True
    return all(constraints(row)) and (
        filename != "transfer-neg.model" or sum(value.startswith("~") for value in row) <= 1
    )


def projections(rows, order):
    return {
        tuple((index, row[index]) for index in indexes)
        for row in rows
        for indexes in combinations(range(len(row)), order)
    }


def errors(row):
    """All applicable broken rules in priority order, before error masking.

    Even a frozen account exists and has a balance. A missing source has no
    monetary state, so its balance/limit predicates are not evaluated. In the
    unconstrained control, Missing + Same materializes two equal missing IDs:
    both existence rules and the different-ID rule are then violated.
    """
    source, target, amount, currency, daily = clean(row)
    source_exists = source != "F_MISSING"
    same_id = target == "T_SAME"
    target_exists = target != "T_MISSING" and (not same_id or source_exists)
    # A1 materialization: USD amount is D; EUR amount is D / 0.50.
    rate = Decimal(1) if currency == "C_USD" else Decimal("0.50")
    number = AMOUNTS[amount] / rate
    converted = number * rate
    rules = (
        (not source_exists, "ERROR_NO_FROM"),
        (source == "F_FROZEN", "ERROR_FROZEN"),
        (not target_exists, "ERROR_NO_TO"),
        (same_id, "ERROR_SAME_ACCOUNT"),
        (number <= 0, "ERROR_BAD_AMOUNT"),
        (source_exists and converted > Decimal(100), "ERROR_INSUFFICIENT"),
        (source_exists and converted > REMAINING[daily], "ERROR_LIMIT"),
    )
    return [name for applies, name in rules if applies]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", action="store_true", help="also check deterministic PICT output")
    args = parser.parse_args()
    transfer_universe = None
    print("file | rows | feasible full rows | covered/required tuples | happy | error-count histogram | max ~")
    for model, result, order in JOBS:
        params = read_model(model)
        saved = ROOT / "results" / result
        with saved.open(newline="") as stream:
            reader = csv.reader(stream, delimiter="\t")
            require(next(reader) == list(params), f"{result}: header mismatch")
            rows = [tuple(row) for row in reader]
        require(len(rows) == len(set(rows)), f"{result}: duplicate rows")
        universe = [row for row in product(*params.values()) if permitted(model, row)]
        require(set(rows) <= set(universe), f"{result}: unknown value or invalid constraint")
        required, covered = projections(universe, order), projections(rows, order)
        require(covered == required, f"{result}: {len(required - covered)} missing tuples")
        if args.repeat:
            command = ["pict", model] + ([f"/o:{order}"] if order != 2 else [])
            regenerated = subprocess.run(command, cwd=ROOT, capture_output=True, check=True)
            require(regenerated.stdout == saved.read_bytes(), f"{result}: nonidentical repeat")

        if model.startswith("browser"):
            print(f"{result} | {len(rows)} | {len(universe)} | {len(covered)}/{len(required)} | - | - | -")
            continue

        histogram = Counter(len(errors(row)) for row in rows)
        outcomes = Counter(errors(row)[0] if errors(row) else "OK" for row in rows)
        max_markers = max(sum(value.startswith("~") for value in row) for row in rows)
        print(f"{result} | {len(rows)} | {len(universe)} | {len(covered)}/{len(required)} | "
              f"{histogram[0]} | {dict(sorted(histogram.items()))} | {max_markers}")
        print(f"  Oracle outcomes: {dict(sorted(outcomes.items()))}")
        print(f"  C1 violations: {sum(not constraints(row)[0] for row in rows)}; "
              f"C2 violations: {sum(not constraints(row)[1] for row in rows)}")
        if model == "transfer.model":
            transfer_universe = set(universe)
        if model == "transfer-neg.model":
            positives = lambda row: all(not value.startswith("~") for value in row)
            positive_required = projections([row for row in universe if positives(row)], 2)
            positive_covered = projections([row for row in rows if positives(row)], 2)
            require(positive_covered == positive_required, "positive-only coverage incomplete")
            print(f"  Positive-only pairs: {len(positive_covered)}/{len(positive_required)}")

    manual = []
    for line in (ROOT / "README.md").read_text().splitlines():
        if not re.match(r"\| (?:\*\*)?S\d\d(?:\s|\*|\[)", line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        row = tuple(re.search(r"`([^`]+)`", cells[index]).group(1) for index in range(1, 6))
        require(row in transfer_universe, f"{cells[0]}: A2 constraint violation")
        row_errors = errors(row)
        expected = "200 " + row_errors[0] if row_errors else "201 OK"
        require(cells[6].strip("*`") == expected, f"{cells[0]}: oracle mismatch")
        manual.append(row)
    require(len(manual) == 25, "A3 should contain 25 rows")
    required = projections(transfer_universe, 2)
    covered = projections(manual, 2)
    print(f"A3: {len(manual)} rows; {len(covered)}/{len(required)} feasible pairs covered.")
    if args.repeat:
        print("All five PICT results reproduced byte-for-byte without /r.")


if __name__ == "__main__":
    main()
