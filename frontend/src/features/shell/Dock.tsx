import { useRef, type CSSProperties, type ReactNode } from "react";
import { workspaceDock, usePreferences } from "../../shared/preferences";

export function PanelTabs({
  name,
  tabs,
  active,
  onChange,
}: {
  name: string;
  tabs: string[];
  active: string;
  onChange: (tab: string) => void;
}) {
  return (
    <div className="panel-tabs" role="tablist" aria-label={name}>
      {tabs.map((tab, i) => (
        <button
          key={tab}
          role="tab"
          id={`${name}-${tab}-tab`}
          aria-controls={`${name}-view`}
          aria-selected={active === tab}
          tabIndex={active === tab ? 0 : -1}
          onClick={() => onChange(tab)}
          onKeyDown={(e) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key))
              return;
            e.preventDefault();
            const n =
              e.key === "Home"
                ? 0
                : e.key === "End"
                  ? tabs.length - 1
                  : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %
                    tabs.length;
            onChange(tabs[n]);
            document.getElementById(`${name}-${tabs[n]}-tab`)?.focus();
          }}
        >
          {tab}
        </button>
      ))}
    </div>
  );
}

export function Seam({
  label,
  axis,
  value,
  min,
  max,
  reverse,
  onChange,
}: {
  label: string;
  axis: "x" | "y";
  value: number;
  min: number;
  max: number;
  reverse?: boolean;
  onChange: (v: number) => void;
}) {
  const drag = useRef<{ start: number; initial: number } | null>(null);
  const update = (v: number) =>
    onChange(Math.round(Math.max(min, Math.min(max, v))));
  return (
    <div
      className={`dock-seam seam-${axis}`}
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      onPointerDown={(e) => {
        e.preventDefault();
        e.currentTarget.focus();
        drag.current = {
          start: axis === "x" ? e.clientX : e.clientY,
          initial: value,
        };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (drag.current)
          update(
            drag.current.initial +
              ((axis === "x" ? e.clientX : e.clientY) - drag.current.start) *
                (reverse ? -1 : 1),
          );
      }}
      onPointerUp={() => {
        drag.current = null;
      }}
      onLostPointerCapture={() => {
        drag.current = null;
      }}
      onKeyDown={(e) => {
        const keys =
          axis === "x" ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
        if (keys.includes(e.key)) {
          e.preventDefault();
          update(value + (e.key === keys[1] ? 10 : -10));
        }
        if (e.key === "Home" || e.key === "End") {
          e.preventDefault();
          update(e.key === "Home" ? min : max);
        }
      }}
    />
  );
}

export function Dock({
  workspace,
  project,
  monitors,
  timeline,
  inspector,
}: {
  workspace: string;
  project: ReactNode;
  monitors: ReactNode;
  timeline: ReactNode;
  inspector: ReactNode;
}) {
  const preferences = usePreferences();
  const layout = preferences.layouts[workspace] ?? workspaceDock(workspace);
  const set = (patch: Parameters<typeof preferences.setLayout>[1]) =>
    preferences.setLayout(workspace, patch);
  return (
    <div className="dock-viewport">
      <main
        className="studio-dock"
        aria-label={`${workspace[0].toUpperCase() + workspace.slice(1)} workspace`}
        data-workspace={workspace}
        data-layout={layout.mode}
        style={
          {
            "--dock-left": `${layout.left}px`,
            "--dock-right":
              workspace === "agent" && !preferences.layouts.agent
                ? "clamp(540px, 46vw, 800px)"
                : `${layout.right}px`,
            "--dock-monitor": `${layout.monitor}px`,
          } as CSSProperties
        }
      >
        <section
          className="dock-panel dock-project"
          tabIndex={0}
          aria-label="Project panel"
        >
          {project}
        </section>
        <div className="dock-left-seam">
          <Seam
            label="Resize Project panel"
            axis="x"
            value={layout.left}
            min={170}
            max={360}
            onChange={(left) => set({ left })}
          />
        </div>
        <div className="dock-monitors">{monitors}</div>
        <div className="dock-horizontal-seam">
          <Seam
            label="Resize monitors and timeline"
            axis="y"
            value={layout.monitor}
            min={180}
            max={475}
            onChange={(monitor) => set({ monitor })}
          />
        </div>
        <section
          className="dock-panel dock-timeline"
          tabIndex={0}
          aria-label="Timeline panel"
        >
          {timeline}
        </section>
        <div className="dock-right-seam">
          <Seam
            label="Resize Chat and Properties panel"
            axis="x"
            value={
              workspace === "agent" && !preferences.layouts.agent
                ? 660
                : layout.right
            }
            min={workspace === "agent" ? 540 : 280}
            max={workspace === "agent" ? 800 : 560}
            reverse
            onChange={(right) => set({ right })}
          />
        </div>
        <section
          className="dock-panel dock-inspector"
          tabIndex={0}
          aria-label="Chat, Properties and Jobs panel"
        >
          {inspector}
        </section>
      </main>
    </div>
  );
}
