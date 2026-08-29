"""The monthly report, as a PDF.

02-PORTAL.md calls it the retention artifact. It is the thing a contractor
forwards to his wife, and the reason he does not cancel in month four.

**It is built from the same narrative the portal renders.** One source of
sentences, so the PDF and the screen cannot disagree — a report that says one
thing on screen and another in the attachment is a report nobody trusts.

**No dependency.** The PDF is written by hand rather than through ReportLab or
WeasyPrint, because both are large, one needs system libraries that make the
Fly image heavier, and what this document actually is is nine lines of text and
one big number. Hand-writing it keeps the deploy small and the output
byte-identical between runs, which makes it testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from mabel_domain.money import Money

# US Letter at 72 dpi, which is the PDF default user space.
PAGE_WIDTH = 612
PAGE_HEIGHT = 792
MARGIN = 72


@dataclass(frozen=True, slots=True)
class ReportFigures:
    """Everything on the page. Every money field is integer cents."""

    business_name: str
    period_start: date
    period_end: date
    calls_answered: int
    leads_created: int
    emergencies: int
    jobs_won: int
    won_value_cents: int
    source_breakdown: dict[str, int]
    untouched_count: int
    oldest_untouched_days: int | None
    plan_price_cents: int


def _escape(text: str) -> str:
    """PDF strings are parenthesised, so parentheses and backslashes escape.

    A business called "Ray's (Lakewood) Plumbing" would otherwise produce a
    corrupt document, and the person who finds out is the customer.
    """
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_lines(figures: ReportFigures) -> list[tuple[str, int]]:
    """The document, as (text, font size) pairs.

    Shares its wording with the portal's narrative deliberately. Both are
    deterministic code reading integers — no model writes any of this, which is
    invariant 4 and also why the numbers are believable.
    """
    month = figures.period_start.strftime("%B %Y")
    value = Money(figures.won_value_cents)
    paid = Money(figures.plan_price_cents)

    lines: list[tuple[str, int]] = [
        (figures.business_name, 14),
        (month, 28),
        ("", 12),
    ]

    if figures.calls_answered == 0:
        # The honest version. A report that is always good news gets ignored,
        # and then the good months stop landing either.
        lines += [
            ("Mabel didn't answer any calls this month.", 16),
            ("", 10),
            ("If that's a surprise, it's usually call forwarding.", 12),
            ("Check the Settings screen in your portal.", 12),
        ]
        return lines

    lines.append(
        (
            f"Mabel answered {figures.calls_answered} "
            f"call{'s' if figures.calls_answered != 1 else ''} after hours.",
            18,
        )
    )
    lines.append(("Before Mabel, those went to voicemail.", 12))
    lines.append(("", 10))

    if figures.jobs_won:
        lines.append(
            (
                f"{figures.leads_created} became leads. You marked {figures.jobs_won} won:",
                14,
            )
        )
        # The number he opens the PDF for.
        lines.append((value.format_whole(), 36))
    else:
        lines.append((f"{figures.leads_created} became leads.", 14))
        lines.append(("None marked won yet.", 12))

    lines.append(("", 10))
    lines.append((f"Emergencies handled: {figures.emergencies}", 12))

    if figures.source_breakdown:
        ranked = sorted(figures.source_breakdown.items(), key=lambda pair: -pair[1])
        lines.append(
            (
                "Where they came from: "
                + "   ".join(f"{name.title()} {count}" for name, count in ranked[:5]),
                12,
            )
        )

    if figures.untouched_count:
        lines.append(("", 10))
        oldest = (
            f", oldest {figures.oldest_untouched_days} days"
            if figures.oldest_untouched_days is not None
            else ""
        )
        lines.append(
            (
                f"Still waiting on you: {figures.untouched_count} "
                f"lead{'s' if figures.untouched_count != 1 else ''}{oldest}.",
                12,
            )
        )

    if figures.jobs_won:
        lines.append(("", 14))
        # The sentence the whole document exists for.
        lines.append(
            (
                f"You paid {paid.format_whole()}. "
                f"{figures.jobs_won} won job{'s' if figures.jobs_won != 1 else ''} "
                f"came to {value.format_whole()}.",
                14,
            )
        )

    return lines


def render_pdf(figures: ReportFigures) -> bytes:
    """A one-page PDF, built by hand.

    Deterministic: the same figures produce byte-identical output, which is
    what makes it worth testing at all.
    """
    lines = build_lines(figures)

    content_parts = ["BT"]
    y = PAGE_HEIGHT - MARGIN
    for text, size in lines:
        y -= size + 6
        if not text:
            continue
        content_parts.append(f"/F1 {size} Tf")
        content_parts.append(f"1 0 0 1 {MARGIN} {y} Tm")
        content_parts.append(f"({_escape(text)}) Tj")
    content_parts.append("ET")
    content = "\n".join(content_parts).encode("latin-1", errors="replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()

    return bytes(out)


def storage_path(tenant_id: str, period_start: date) -> str:
    """Where the PDF lives in the private bucket.

    Partitioned by tenant, like recordings, so a retention sweep or a tenant
    deletion is a prefix operation.
    """
    return f"{tenant_id}/reports/{period_start:%Y-%m}.pdf"
