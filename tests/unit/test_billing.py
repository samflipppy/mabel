"""Billing. Integer cents, and a PDF that is byte-identical run to run."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date

import pytest
from mabel_billing.plans import (
    PLANS,
    PlanKey,
    estimate_invoice,
    plan,
    stripe_price_id,
)
from mabel_billing.report_pdf import ReportFigures, build_lines, render_pdf, storage_path
from mabel_billing.stripe_client import (
    WEBHOOK_TOLERANCE_SECONDS,
    FakeStripeClient,
    StripeRefusedUnderTest,
    StripeUnavailable,
    WebhookVerificationFailed,
    api_key,
    is_configured,
    verify_webhook,
)

NOW = 1_800_000_000.0
SECRET = "whsec_test_not_a_real_secret"


def figures(**overrides) -> ReportFigures:
    base = {
        "business_name": "Ruiz Plumbing",
        "period_start": date(2026, 10, 1),
        "period_end": date(2026, 10, 31),
        "calls_answered": 23,
        "leads_created": 14,
        "emergencies": 3,
        "jobs_won": 5,
        "won_value_cents": 1_460_000,
        "source_breakdown": {"google": 11, "referral": 6, "truck": 3},
        "untouched_count": 2,
        "oldest_untouched_days": 9,
        "plan_price_cents": 29_900,
    }
    return ReportFigures(**(base | overrides))


class TestPlans:
    def test_every_price_is_integer_cents(self):
        for option in PLANS.values():
            assert isinstance(option.price_cents, int)
            assert isinstance(option.overage_cents_per_min, int)

    def test_the_headline_plan_is_the_one_the_cost_model_assumes(self):
        # 00-STACK.md's margin table is built on $299.
        assert plan(PlanKey.MABEL).price_cents == 29_900

    def test_included_minutes_sit_above_a_typical_month(self):
        """00-STACK.md assumes ~90 voice minutes a month. The allowance is well
        above that so an ordinary month never produces an overage line — the
        cheapest way to avoid a surprise bill is to not generate one."""
        assert plan(PlanKey.MABEL).included_minutes > 90

    def test_an_unknown_plan_raises(self):
        with pytest.raises(ValueError, match="unknown plan"):
            plan("enterprise")

    def test_price_ids_are_not_hardcoded(self, monkeypatch):
        # They differ between Stripe test and live mode, and a hardcoded one is
        # how a test-mode id reaches production.
        monkeypatch.delenv("STRIPE_PRICE_MABEL", raising=False)
        assert stripe_price_id(PlanKey.MABEL) is None
        monkeypatch.setenv("STRIPE_PRICE_MABEL", "price_abc")
        assert stripe_price_id(PlanKey.MABEL) == "price_abc"


class TestOverage:
    def test_inside_the_allowance_costs_nothing(self):
        invoice = estimate_invoice(PlanKey.MABEL, 120)
        assert invoice.overage_cents == 0
        assert invoice.is_over is False

    def test_over_the_allowance_is_charged_per_minute(self):
        option = plan(PlanKey.MABEL)
        invoice = estimate_invoice(PlanKey.MABEL, option.included_minutes + 10)
        assert invoice.overage_cents == 10 * option.overage_cents_per_min

    def test_a_partial_minute_rounds_up(self):
        """A customer who used 0.2 minutes over used a minute we paid for.
        Rounding down means eating the difference on every invoice."""
        option = plan(PlanKey.MABEL)
        invoice = estimate_invoice(PlanKey.MABEL, option.included_minutes + 0.2)
        assert invoice.overage_cents == option.overage_cents_per_min

    def test_the_total_stays_an_integer(self):
        invoice = estimate_invoice(PlanKey.MABEL, 400.7)
        assert isinstance(invoice.total_cents, int)
        assert invoice.total_cents == invoice.plan_cents + invoice.overage_cents

    def test_exactly_at_the_allowance_is_not_over(self):
        option = plan(PlanKey.MABEL)
        assert estimate_invoice(PlanKey.MABEL, option.included_minutes).overage_cents == 0


class TestFailsClosed:
    def test_no_key_means_no_client(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with pytest.raises(StripeUnavailable, match="BLOCKED"):
            api_key()

    def test_is_configured_reports_honestly(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        assert is_configured() is False
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        assert is_configured() is True

    def test_the_client_refuses_under_pytest(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        from mabel_billing.stripe_client import StripeClient

        with pytest.raises(StripeRefusedUnderTest, match="real subscription"):
            StripeClient()


class TestWebhookVerification:
    def sign(self, body: bytes, timestamp: int = int(NOW), secret: str = SECRET) -> str:
        digest = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        return f"t={timestamp},v1={digest}"

    def test_a_correctly_signed_event_verifies(self):
        body = json.dumps({"id": "evt_1", "type": "invoice.paid"}).encode()
        event = verify_webhook(body, self.sign(body), secret=SECRET, now=NOW)
        assert event["id"] == "evt_1"

    def test_a_re_serialised_body_does_not_verify(self):
        body = b'{"id":"evt_1","type":"invoice.paid"}'
        header = self.sign(body)
        round_tripped = json.dumps(json.loads(body)).encode()
        assert round_tripped != body
        with pytest.raises(WebhookVerificationFailed):
            verify_webhook(round_tripped, header, secret=SECRET, now=NOW)

    def test_a_dict_is_refused_outright(self):
        with pytest.raises(WebhookVerificationFailed, match="must be bytes"):
            verify_webhook({"id": "evt_1"}, "t=1,v1=x", secret=SECRET)  # type: ignore[arg-type]

    def test_an_old_event_is_refused(self):
        body = b"{}"
        header = self.sign(body, timestamp=int(NOW - WEBHOOK_TOLERANCE_SECONDS - 1))
        with pytest.raises(WebhookVerificationFailed, match="tolerance"):
            verify_webhook(body, header, secret=SECRET, now=NOW)

    def test_a_forged_signature_is_refused(self):
        body = b"{}"
        with pytest.raises(WebhookVerificationFailed, match="did not match"):
            verify_webhook(body, f"t={int(NOW)},v1=deadbeef", secret=SECRET, now=NOW)

    def test_a_malformed_header_is_refused(self):
        with pytest.raises(WebhookVerificationFailed, match="malformed"):
            verify_webhook(b"{}", "nonsense", secret=SECRET, now=NOW)

    def test_the_wrong_secret_is_refused(self):
        body = b"{}"
        with pytest.raises(WebhookVerificationFailed):
            verify_webhook(body, self.sign(body), secret="whsec_other", now=NOW)


class TestFakeClient:
    async def test_it_records_and_charges_nothing(self):
        client = FakeStripeClient()
        customer = await client.create_customer(email="ray@example.com", business_name="Ruiz")
        assert customer == "cus_fake"
        url = await client.create_checkout_session(
            customer_id=customer,
            price_id="price_x",
            success_url="https://x",
            cancel_url="https://y",
        )
        assert url.startswith("https://checkout.stripe.test")
        assert client.calls is not None
        assert [name for name, _ in client.calls] == [
            "create_customer",
            "create_checkout_session",
        ]

    async def test_usage_reports_carry_an_idempotency_key(self):
        """A retried usage report without one double-bills, and a customer who
        was double-billed does not come back."""
        client = FakeStripeClient()
        await client.report_overage(
            subscription_item_id="si_1", minutes=12, idempotency_key="2026-10:tenant"
        )
        assert client.calls is not None
        _name, kwargs = client.calls[0]
        assert kwargs["idempotency_key"]


class TestTheReportPdf:
    def test_it_is_a_pdf(self):
        pdf = render_pdf(figures())
        assert pdf.startswith(b"%PDF-1.4")
        assert pdf.rstrip().endswith(b"%%EOF")

    def test_it_is_byte_identical_run_to_run(self):
        # Determinism is what makes it worth testing at all.
        assert render_pdf(figures()) == render_pdf(figures())

    def test_the_xref_offsets_point_at_their_objects(self):
        """A wrong offset produces a file that opens in some readers and not
        others, which is the worst way to find out."""
        pdf = render_pdf(figures())
        start = int(pdf.split(b"startxref")[1].split()[0])
        xref = pdf[start:].split(b"trailer")[0].decode()
        rows = [row for row in xref.splitlines() if row.endswith(" n ")]
        assert len(rows) == 5
        for index, row in enumerate(rows, start=1):
            offset = int(row.split()[0])
            assert pdf[offset:].startswith(f"{index} 0 obj".encode())

    def test_a_business_name_with_parentheses_does_not_corrupt_it(self):
        """PDF strings are parenthesised. "Ray's (Lakewood) Plumbing" would
        otherwise produce a broken file, and the person who finds out is the
        customer."""
        pdf = render_pdf(figures(business_name="Ray's (Lakewood) Plumbing"))
        assert rb"Ray's \(Lakewood\) Plumbing" in pdf
        assert pdf.startswith(b"%PDF")

    def test_the_headline_number_is_the_won_value(self):
        lines = build_lines(figures())
        assert ("$14,600", 36) in lines

    def test_the_closing_sentence_is_the_point_of_the_document(self):
        text = " ".join(line for line, _ in build_lines(figures()))
        assert "You paid $299." in text
        assert "came to $14,600." in text

    def test_a_month_with_no_calls_says_so_plainly(self):
        """A report that is always good news gets ignored, and then the good
        months stop landing either."""
        text = " ".join(line for line, _ in build_lines(figures(calls_answered=0)))
        assert "didn't answer any calls" in text
        assert "call forwarding" in text

    def test_a_month_with_no_wins_does_not_invent_a_figure(self):
        text = " ".join(line for line, _ in build_lines(figures(jobs_won=0, won_value_cents=0)))
        assert "None marked won yet." in text
        assert "$0" not in text

    def test_the_waiting_line_appears_when_leads_are_untouched(self):
        text = " ".join(line for line, _ in build_lines(figures()))
        assert "Still waiting on you: 2 leads, oldest 9 days." in text

    def test_it_is_absent_when_nothing_is_waiting(self):
        text = " ".join(
            line for line, _ in build_lines(figures(untouched_count=0, oldest_untouched_days=None))
        )
        assert "Still waiting" not in text

    def test_every_figure_traces_to_an_integer_cents_column(self):
        # No float formatting anywhere: the two money strings are produced by
        # Money.format_whole from value_cents and price_cents.
        text = " ".join(line for line, _ in build_lines(figures()))
        assert "14600.0" not in text
        assert "299.0" not in text

    def test_the_storage_path_is_partitioned_by_tenant(self):
        path = storage_path("abc-123", date(2026, 10, 1))
        assert path == "abc-123/reports/2026-10.pdf"
