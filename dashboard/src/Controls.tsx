import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, MoreHorizontal } from "lucide-react";
import { tabKeys } from "./a11y";

export function SectionTabs({
  label,
  value,
  items,
  onChange,
  children,
}: {
  label: string;
  value: string;
  items: { id: string; label: string }[];
  onChange: (id: string) => void;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <>
      <div
        className="detail-tabs section-tabs"
        role="tablist"
        aria-label={label}
        onKeyDown={tabKeys}
      >
        {items.map((item) => (
          <button
            key={item.id}
            role="tab"
            id={id + item.id}
            aria-selected={item.id === value}
            tabIndex={item.id === value ? 0 : -1}
            aria-controls={id + "-panel"}
            onClick={() => onChange(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        className="settings-panel"
        role="tabpanel"
        id={id + "-panel"}
        aria-labelledby={id + value}
      >
        {children}
      </div>
    </>
  );
}

export function ActionMenu({
  label = "More",
  iconOnly = false,
  children,
}: {
  label?: string;
  iconOnly?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node))
        ref.current?.removeAttribute("open");
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);
  return (
    <details
      className="action-menu"
      ref={ref}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          ref.current?.removeAttribute("open");
          ref.current?.querySelector("summary")?.focus();
          e.stopPropagation();
        }
      }}
    >
      <summary
        className={iconOnly ? "icon-only" : undefined}
        aria-label={label}
        title={iconOnly ? label : undefined}
      >
        <MoreHorizontal size={17} aria-hidden="true" />
        {!iconOnly && label}
      </summary>
      <div
        className="action-menu-items"
        onClick={(e) => {
          if ((e.target as Element).closest("button")) {
            ref.current?.removeAttribute("open");
            ref.current?.querySelector("summary")?.focus();
          }
        }}
      >
        {children}
      </div>
    </details>
  );
}

export function SettingsDisclosure({
  title,
  summary,
  children,
  forceOpen = false,
  initialOpen = false,
}: {
  title: string;
  summary?: string;
  children: ReactNode;
  forceOpen?: boolean;
  initialOpen?: boolean;
}) {
  const [open, setOpen] = useState(initialOpen || forceOpen);
  useEffect(() => {
    if (forceOpen) setOpen(true);
  }, [forceOpen]);
  return (
    <details
      className="settings-disclosure"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>
        <span>
          <strong>{title}</strong>
          {summary && <small>{summary}</small>}
        </span>
        <ChevronDown size={16} />
      </summary>
      <div className="disclosure-content">{children}</div>
    </details>
  );
}
