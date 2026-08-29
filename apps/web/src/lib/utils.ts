import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Money for the report. Integer cents only. Never float. Never from a model. */
export function formatCents(cents: bigint): string {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  const dollars = abs / 100n;
  const remainder = abs % 100n;
  const sign = negative ? "-" : "";
  return `${sign}$${dollars.toString()}.${remainder.toString().padStart(2, "0")}`;
}
