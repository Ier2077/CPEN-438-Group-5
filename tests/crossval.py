#!/usr/bin/env python3
"""Compare a Logisim trace with the cycle-level simulator trace."""

import argparse
import csv
import sys
from pathlib import Path


CONTROL_SIGNALS = (
    "PC", "ForwardA", "ForwardB", "ForwardC", "ForwardD", "Stall", "Flush"
)
DATA_SIGNALS = ("ALU_EX", "RegWrite_WB", "WriteReg", "WriteData")
COLUMN_ALIASES = {"PC": ("PC", "IF")}


def read_trace(path):
    with Path(path).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"{path}: trace is empty")
    return rows


def parse_value(signal, value, pc_radix):
    text = value.strip().strip('"')
    if text in {"", "-", "bub"}:
        return text
    if signal == "PC":
        base = 16 if pc_radix == "hex" else 10
    elif set(text) <= {"0", "1"} and len(text) > 1:
        base = 2
    elif text.lower().startswith("0x"):
        base = 16
    else:
        base = 10
    return int(text, base)


def compare(c_rows, l_rows, signals, pc_radix, skip):
    c_start = max(0, -skip)
    l_start = max(0, skip)
    c_rows = c_rows[c_start:]
    l_rows = l_rows[l_start:]
    count = min(len(c_rows), len(l_rows))
    mismatches = []
    for index in range(count):
        c_row = c_rows[index]
        l_row = l_rows[index]
        for signal in signals:
            left = parse_value(signal, get_value(c_row, signal), pc_radix)
            right = parse_value(signal, get_value(l_row, signal), pc_radix)
            if left != right:
                mismatches.append((index, signal, left, right))
    return mismatches, len(c_rows), len(l_rows)


def check_columns(rows, path, signals):
    missing = [signal for signal in signals
               if not any(column in rows[0] for column in COLUMN_ALIASES.get(signal, (signal,)))]
    if missing:
        raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")


def get_value(row, signal):
    for column in COLUMN_ALIASES.get(signal, (signal,)):
        if column in row:
            return row[column]
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c-trace", required=True, type=Path)
    parser.add_argument("--logisim", required=True, type=Path)
    parser.add_argument("--pc-radix", choices=("hex", "decimal"), default="decimal")
    parser.add_argument("--skip", type=int, help="leading rows to skip in Logisim (negative skips C)")
    parser.add_argument("--control-only", action="store_true", help="interim seven-signal check")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    signals = CONTROL_SIGNALS if args.control_only else CONTROL_SIGNALS + DATA_SIGNALS
    c_rows = read_trace(args.c_trace)
    l_rows = read_trace(args.logisim)
    check_columns(c_rows, args.c_trace, signals)
    check_columns(l_rows, args.logisim, signals)

    candidates = [args.skip] if args.skip is not None else list(range(-3, 4))
    best = None
    for skip in candidates:
        mismatches, c_len, l_len = compare(c_rows, l_rows, signals, args.pc_radix, skip)
        score = (len(mismatches), abs(c_len - l_len))
        if best is None or score < best[0]:
            best = (score, skip, mismatches, c_len, l_len)

    _, skip, mismatches, c_len, l_len = best
    status = "PASS" if not mismatches and c_len == l_len else "FAIL"
    lines = [
        f"CROSS-VALIDATION: {status}",
        f"signals: {len(signals)} ({', '.join(signals)})",
        f"rows: C={c_len} Logisim={l_len}",
        f"skip: {skip}",
        f"mismatches: {len(mismatches)}",
    ]
    for index, signal, left, right in mismatches[:20]:
        lines.append(f"cycle-index {index}: {signal}: C={left!r} Logisim={right!r}")
    output = "\n".join(lines) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output)
    print(output, end="")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"crossval.py: {error}", file=sys.stderr)
        sys.exit(2)