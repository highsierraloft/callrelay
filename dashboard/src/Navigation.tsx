import { useCallback, useEffect, useRef, useState } from "react";
import {
  AudioLines,
  CalendarClock,
  PanelLeftClose,
  PanelLeftOpen,
  Phone,
  PhoneIncoming,
  PhoneOutgoing,
  Plug,
  Settings,
  SlidersHorizontal,
  Square,
  Users,
  Wallet,
} from "lucide-react";

export const sections = [
  { id: "calls", label: "Calls", icon: Phone, group: "Activity" },
  { id: "contacts", label: "Contacts", icon: Users, group: "Activity" },
  {
    id: "callbacks",
    label: "Callbacks",
    icon: CalendarClock,
    group: "Activity",
  },
  { id: "costs", label: "Costs", icon: Wallet, group: "Activity" },
  { id: "inbound", label: "Inbound", icon: PhoneIncoming, group: "Agent" },
  { id: "outbound", label: "Outbound", icon: PhoneOutgoing, group: "Agent" },
  {
    id: "personalities",
    label: "Personalities",
    icon: SlidersHorizontal,
    group: "Agent",
  },
  {
    id: "playground",
    label: "Playground",
    icon: AudioLines,
    group: "Workspace",
  },
  { id: "integration", label: "Integrations", icon: Plug, group: "Workspace" },
  { id: "settings", label: "Settings", icon: Settings, group: "Workspace" },
];
const preferenceKey = "calling.sidebar";

export function useNavigation() {
  const [expanded, updateExpanded] = useState(() => {
    try {
      const saved = localStorage.getItem(preferenceKey);
      if (saved === "expanded" || saved === "collapsed")
        return saved === "expanded";
    } catch {
      /* Storage can be unavailable in private browser contexts. */
    }
    return matchMedia("(min-width: 1100px)").matches;
  });
  const [compact, setCompact] = useState(
    () => matchMedia("(max-width: 560px)").matches,
  );
  const setExpanded = useCallback((next: boolean) => {
    updateExpanded(next);
    try {
      localStorage.setItem(preferenceKey, next ? "expanded" : "collapsed");
    } catch {
      /* Navigation remains usable without persistent storage. */
    }
  }, []);
  useEffect(() => {
    const media = matchMedia("(max-width: 560px)");
    const resize = () => setCompact(media.matches);
    const sync = (event: StorageEvent) => {
      if (
        event.key === preferenceKey &&
        ["expanded", "collapsed"].includes(event.newValue || "")
      )
        updateExpanded(event.newValue === "expanded");
    };
    media.addEventListener("change", resize);
    window.addEventListener("storage", sync);
    return () => {
      media.removeEventListener("change", resize);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const closeOverlay = useCallback(() => {
    if (compact) setExpanded(false);
  }, [compact, setExpanded]);
  return { expanded, setExpanded, overlay: compact && expanded, closeOverlay };
}

export default function Navigation({
  navigation,
  selected,
  onNavigate,
  endCall,
  ending,
  sourceLine,
}: {
  navigation: ReturnType<typeof useNavigation>;
  selected: string;
  onNavigate: (path: string) => void;
  endCall?: () => void;
  ending: boolean;
  sourceLine: string;
}) {
  const panel = useRef<HTMLElement>(null),
    toggle = useRef<HTMLButtonElement>(null);
  const { expanded, setExpanded, overlay } = navigation;
  useEffect(() => {
    if (!overlay) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setExpanded(false);
        toggle.current?.focus();
      }
    };
    window.addEventListener("keydown", escape);
    toggle.current?.focus();
    return () => window.removeEventListener("keydown", escape);
  }, [overlay, setExpanded]);
  const label = expanded ? "Collapse sidebar" : "Expand sidebar";
  return (
    <>
      <aside
        className={"sidebar" + (overlay ? " sidebar-overlay" : "")}
        ref={panel}
        role={overlay ? "dialog" : undefined}
        aria-modal={overlay || undefined}
        aria-label="Navigation"
        onKeyDown={(event) => {
          if (!overlay || event.key !== "Tab") return;
          const controls = [
            ...(panel.current?.querySelectorAll<HTMLElement>(
              "button:not(:disabled), a[href]",
            ) || []),
          ].filter((el) => el.getClientRects().length);
          const first = controls[0],
            last = controls[controls.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }}
      >
        <div className="sidebar-heading">
          <button
            className="sidebar-toggle"
            ref={toggle}
            aria-label={label}
            title={label}
            aria-expanded={expanded}
            aria-controls="workspace-navigation"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? (
              <PanelLeftClose size={20} />
            ) : (
              <PanelLeftOpen size={20} />
            )}
          </button>
          <span className="sidebar-title">CallRelay</span>
        </div>
        <nav id="workspace-navigation" aria-label="Main navigation">
          {["Activity", "Agent", "Workspace"].map((group) => (
            <section className="nav-group" key={group} aria-label={group}>
              <h2>{group}</h2>
              {sections
                .filter((item) => item.group === group)
                .map((item) => (
                  <a
                    key={item.id}
                    href={"/" + item.id}
                    aria-label={item.label}
                    title={!expanded ? item.label : undefined}
                    aria-current={selected === item.id ? "page" : undefined}
                    onClick={(event) => {
                      event.preventDefault();
                      onNavigate("/" + item.id);
                    }}
                  >
                    <item.icon size={19} aria-hidden="true" />
                    <span className="nav-label">{item.label}</span>
                  </a>
                ))}
            </section>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {overlay && endCall ? (
            <button className="danger" disabled={ending} onClick={endCall}>
              <Square size={15} />
              {ending ? "Ending…" : "End Active Call"}
            </button>
          ) : (
            <span className="sidebar-line" title={sourceLine}>
              <Phone size={16} aria-hidden="true" />
              <span className="nav-label">{sourceLine}</span>
            </span>
          )}
        </div>
      </aside>
      {overlay && (
        <button
          className="sidebar-backdrop"
          tabIndex={-1}
          aria-label="Dismiss navigation"
          onClick={() => {
            setExpanded(false);
            toggle.current?.focus();
          }}
        />
      )}
    </>
  );
}
