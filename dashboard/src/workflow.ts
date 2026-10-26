import type { FollowupStatus, OutcomeStatus } from "./types";
export const outcomes: Record<OutcomeStatus, string> = {
  achieved: "Achieved",
  declined: "Declined",
  incomplete: "Incomplete",
  uncertain: "Uncertain",
};
export const followupStates: Record<FollowupStatus, string> = {
  needs_information: "Needs information",
  ready: "Ready",
  completed: "Completed",
  cancelled: "Cancelled",
};
export const money = (n: number | null | undefined) =>
  n == null
    ? "Not recorded"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 5,
      }).format(n);
export const deviceZone = () =>
  Intl.DateTimeFormat().resolvedOptions().timeZone;
export function localInput(value?: string | null) {
  if (!value) return "";
  const d = new Date(value);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
export function instant(value: string) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime()) || localInput(d.toISOString()) !== value)
    throw new Error("This local time does not exist. Choose another time.");
  return d.toISOString();
}
export function zonedInstant(value: string) {
  const utc = instant(value);
  if (!utc) return null;
  const offset = -new Date(utc).getTimezoneOffset();
  const absolute = Math.abs(offset);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  const minutes = String(absolute % 60).padStart(2, "0");
  return `${value}:00${offset >= 0 ? "+" : "-"}${hours}:${minutes}`;
}
export const lines = (text: string) =>
  text
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

export function date(
  value: string | null | undefined,
  zone: string,
  short = false,
) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    ...(short
      ? { day: "numeric", month: "short" }
      : {
          day: "numeric",
          month: "short",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }),
  }).format(new Date(value));
}
