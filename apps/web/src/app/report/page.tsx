import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCents } from "@/lib/utils";

// Stub totals. Real numbers come from NUMERIC columns the owner entered by hand.
const CALLS_ANSWERED = 0;
const JOBS_BOOKED = 0;
const DOLLARS_WON_CENTS = 0n;

export default function ReportPage() {
  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="text-sm uppercase tracking-wide text-[color:var(--taupe)]">Office</p>
        <h1 className="mt-1 font-serif text-4xl">Monthly report</h1>
        <p className="mt-3 max-w-xl text-[color:var(--taupe)]">
          Calls answered, jobs booked, and dollars the owner marked won. Mabel does not invent a
          dollar figure.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Calls answered" value={String(CALLS_ANSWERED)} />
        <Stat label="Jobs booked" value={String(JOBS_BOOKED)} />
        <Stat label="Marked won" value={formatCents(DOLLARS_WON_CENTS)} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Nothing to total yet</CardTitle>
          <CardDescription>
            When the owner texts WON and an amount, it shows up here. Not before.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[color:var(--taupe)]">No customer names on this page.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-3xl">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}
