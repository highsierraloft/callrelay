import type { KeyboardEvent } from "react";

export function tabKeys(event: KeyboardEvent<HTMLElement>) {
  const tabs = Array.from(
    event.currentTarget.querySelectorAll<HTMLButtonElement>(
      'button[role="tab"]',
    ),
  );
  const index = tabs.indexOf(event.target as HTMLButtonElement);
  if (index < 0) return;
  const next =
    event.key === "ArrowRight"
      ? (index + 1) % tabs.length
      : event.key === "ArrowLeft"
        ? (index + tabs.length - 1) % tabs.length
        : event.key === "Home"
          ? 0
          : event.key === "End"
            ? tabs.length - 1
            : -1;
  if (next < 0) return;
  event.preventDefault();
  tabs[next].focus();
  tabs[next].click();
}
