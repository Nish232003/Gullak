from __future__ import annotations

import re

from gullak.pipeline.contracts import (
    ExtractedLine,
    ExtractedPage,
    ValidationIssue,
    ValidationResult,
)

DEVANAGARI_TO_ASCII = str.maketrans("०१२३४५६७८९", "0123456789")
_LINE_PATTERN = re.compile(
    r"^\s*(?P<name>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>[^\d\s]+)\s+(?P<amount>\d+(?:\.\d+)?)\s*$"
)


def normalize_digits(text: str) -> str:
    """Convert Devanagari digits (०१२३...) to ASCII digits (0123...)."""
    return text.translate(DEVANAGARI_TO_ASCII)


def parse_register_text(line: ExtractedLine) -> ExtractedLine:
    """Normalize digits in line text and fill missing structured fields when possible."""
    norm_text = normalize_digits(line.text).strip()
    norm_name = normalize_digits(line.item_name).strip()
    norm_unit = normalize_digits(line.unit).strip()

    qty = line.qty
    amount = line.amount

    if (not norm_name or qty <= 0 or amount <= 0) and norm_text:
        match = _LINE_PATTERN.match(norm_text)
        if match:
            norm_name = norm_name or match.group("name").strip()
            if qty <= 0:
                qty = float(match.group("qty"))
            norm_unit = norm_unit or match.group("unit").strip()
            if amount <= 0:
                amount = float(match.group("amount"))

    unit_price = line.unit_price
    if unit_price is None and qty > 0 and amount > 0:
        unit_price = round(amount / qty, 4)

    return line.model_copy(
        update={
            "text": norm_text,
            "item_name": norm_name,
            "unit": norm_unit,
            "qty": qty,
            "amount": amount,
            "unit_price": unit_price,
        }
    )


def validate_page(
    page: ExtractedPage,
    recent_avg_prices: dict[str, float] | None = None,
    *,
    total_tolerance: float = 1.0,
    price_deviation_pct: float = 0.50,
    min_confidence: float = 0.70,
) -> ValidationResult:
    """Run Gullak quality gates on an extracted page.

    Quality gates (from project specification):
    1. Native digits (१२३) are converted before any check.
    2. Page date is present.
    3. Quantity and amount are positive on every line.
    4. Sum of extracted line amounts equals written_total within ₹1.
    5. Unit price is within 50% of the item's recent average (if known).
    6. Low-confidence lines are flagged as doubtful for the confirm list.
    """
    issues: list[ValidationIssue] = []
    doubtful_lines: set[int] = set()
    recent_avg_prices = recent_avg_prices or {}

    normalized_lines = [parse_register_text(line) for line in page.lines]
    normalized_page = page.model_copy(update={"lines": normalized_lines})

    if normalized_page.page_date is None:
        issues.append(
            ValidationIssue(
                line_no=None,
                code="missing_date",
                message="Page date is missing.",
            )
        )

    if not normalized_lines:
        issues.append(
            ValidationIssue(
                line_no=None,
                code="empty_page",
                message="No extracted lines found on page.",
            )
        )

    line_sum = 0.0
    for line in normalized_lines:
        if line.qty <= 0:
            issues.append(
                ValidationIssue(
                    line_no=line.line_no,
                    code="non_positive_qty",
                    message=f"Line {line.line_no} quantity must be positive (got {line.qty}).",
                )
            )
            doubtful_lines.add(line.line_no)

        if line.amount <= 0:
            issues.append(
                ValidationIssue(
                    line_no=line.line_no,
                    code="non_positive_amount",
                    message=f"Line {line.line_no} amount must be positive (got {line.amount}).",
                )
            )
            doubtful_lines.add(line.line_no)

        if line.confidence < min_confidence:
            issues.append(
                ValidationIssue(
                    line_no=line.line_no,
                    code="low_confidence",
                    message=f"Line {line.line_no} confidence {line.confidence:.2f} < {min_confidence:.2f}.",
                )
            )
            doubtful_lines.add(line.line_no)

        if line.qty > 0 and line.amount > 0:
            line_sum += line.amount
            unit_price = line.amount / line.qty
            avg_price = recent_avg_prices.get(line.item_name.lower())
            if avg_price and avg_price > 0:
                rel_diff = abs(unit_price - avg_price) / avg_price
                if rel_diff > price_deviation_pct:
                    issues.append(
                        ValidationIssue(
                            line_no=line.line_no,
                            code="price_outlier",
                            message=(
                                f"Line {line.line_no} unit price ₹{unit_price:.2f} deviates "
                                f"{rel_diff * 100:.0f}% from recent avg ₹{avg_price:.2f}."
                            ),
                        )
                    )
                    doubtful_lines.add(line.line_no)

    if normalized_page.written_total is not None:
        diff = abs(line_sum - normalized_page.written_total)
        if diff > total_tolerance:
            issues.append(
                ValidationIssue(
                    line_no=None,
                    code="total_mismatch",
                    message=(
                        f"Sum of lines (₹{line_sum:.2f}) differs from written total "
                        f"(₹{normalized_page.written_total:.2f}) by ₹{diff:.2f} (> ₹{total_tolerance:.2f})."
                    ),
                )
            )

    return ValidationResult(
        passed=len(issues) == 0,
        issues=issues,
        normalized_page=normalized_page,
        doubtful_lines=sorted(doubtful_lines),
    )
