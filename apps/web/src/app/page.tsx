import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function OvernightPage() {
  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="text-sm uppercase tracking-wide text-[color:var(--taupe)]">Office</p>
        <h1 className="mt-1 font-serif text-4xl">Overnight recap</h1>
        <p className="mt-3 max-w-xl text-[color:var(--taupe)]">
          The owner gets a text at 7am. This page is for whoever handles the office. If nobody
          logs in, Mabel still did the job.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>No calls yet</CardTitle>
          <CardDescription>When Mabel takes one, it lands here. Nothing made up.</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[color:var(--taupe)]">
            Emergencies already went out as a text. Everything else waits for morning.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
