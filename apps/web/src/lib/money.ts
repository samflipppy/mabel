/**
 * Money, in the browser. Integer cents in, a string out.
 *
 * The API sends cents and never a formatted string, so formatting happens
 * exactly once, here. Two places formatting the same number is how a report
 * and a dashboard end up disagreeing by a penny.
 *
 * There is deliberately no `parse` in this file. The owner's "What's this job
 * worth?" input is sent to the server as the raw string he typed and parsed
 * there by `parse_owner_amount`, the same code the SMS grammar uses. Parsing
 * in two languages means two sets of rules about whether `38OO` is a number.
 */

/** A job worth more than this is a typo. Mirrors MAX_OWNER_AMOUNT_CENTS. */
export const MAX_AMOUNT_CENTS = 100_000_000;

export class MoneyError extends Error {}

function assertCents(cents: number): void {
  if (!Number.isInteger(cents)) {
    // A float here means something upstream divided by 100 already.
    throw new MoneyError(`money must be integer cents, got ${cents}`);
  }
}

/** `380000` -> `$3,800.00`. */
export function formatMoney(cents: number, currency = "USD"): string {
  assertCents(cents);
  if (currency !== "USD") {
    return `${(cents / 100).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} ${currency}`;
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

/**
 * `380000` -> `$3,800`. What the dashboard cards and the board show.
 *
 * 02-PORTAL.md: "Every dollar figure is large and unmissable." Trailing `.00`
 * on a headline number is noise at that size.
 */
export function formatMoneyWhole(cents: number, currency = "USD"): string {
  assertCents(cents);
  if (currency === "USD" && cents % 100 === 0) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }).format(cents / 100);
  }
  return formatMoney(cents, currency);
}

/** For a value that may not have been entered yet. */
export function formatMoneyOrDash(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return formatMoneyWhole(cents);
}

/**
 * A delta against last month, for the dashboard cards.
 *
 * Returns null when there is nothing to compare against — a first month with
 * no history should show nothing rather than a meaningless +100%.
 */
export function percentDelta(current: number, previous: number): number | null {
  if (previous === 0) return null;
  return Math.round(((current - previous) / previous) * 100);
}
