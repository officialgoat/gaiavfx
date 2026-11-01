import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { Check, ChevronRight } from "lucide-react";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  menuEntries,
  menuNames,
  type Command,
  type MenuEntry,
} from "./commands";
import type { Target } from "../timeline/editor-state";

type Registry = {
  commands: Map<string, Command>;
  target: Target;
  select: (target: Target) => void;
};
const CommandContext = createContext<Registry | null>(null);
export const useCommands = () => useContext(CommandContext)!;
export function shortcutLabel(value: string) {
  return value
    .replace("Mod", navigator.platform.includes("Mac") ? "⌘" : "Ctrl")
    .replace("ArrowLeft", "←")
    .replace("ArrowRight", "→");
}
function matches(e: KeyboardEvent, shortcut: string) {
  const pieces = shortcut.toLowerCase().split("+");
  const key = shortcut === "+" ? "+" : pieces.at(-1);
  return (
    e.key.toLowerCase() === key &&
    !!(e.ctrlKey || e.metaKey) === pieces.includes("mod") &&
    e.altKey === pieces.includes("alt") &&
    (key === "+" || e.shiftKey === pieces.includes("shift"))
  );
}
export function CommandProvider({
  commands,
  target,
  select,
  children,
}: Registry & { children: ReactNode }) {
  useEffect(() => {
    function keydown(e: KeyboardEvent) {
      const element = e.target as HTMLElement;
      if (
        e.defaultPrevented ||
        // Native controls keep their activation keys. Track items deliberately
        // use the editor's Enter shortcut to open their properties.
        ((e.key === "Enter" || e.key === " ") &&
          element.closest("button,a,summary") &&
          !element.closest(".track-item") &&
          !(
            e.key === "Enter" &&
            element.closest("[data-enter=command]") &&
            element.closest("button")?.getAttribute("aria-pressed") === "true"
          )) ||
        (element.closest(
          "input,textarea,select,[contenteditable=true],[role=dialog],[role=menu]",
        ) &&
          !(e.shiftKey && e.key === "Tab" && element.closest(".chat-panel")) &&
          // A slider keeps only its own keys; digits, Enter and letters still reach the commands (round 2, C8).
          !(
            element instanceof HTMLInputElement &&
            element.type === "range" &&
            !/^(Arrow|Home|End|Page)/.test(e.key)
          ))
      )
        return;
      const command = [...commands.values()].find(
        (c) => c.shortcut && matches(e, c.shortcut),
      );
      if (command) {
        e.preventDefault();
        if (!command.reason?.(target)) command.run(target);
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [commands, target]);
  return (
    <CommandContext.Provider value={{ commands, target, select }}>
      {children}
    </CommandContext.Provider>
  );
}
function Items({
  entries,
  target,
  onRun,
}: {
  entries: MenuEntry[];
  target: Target;
  onRun?: (run: () => void) => void;
}) {
  const { commands } = useCommands();
  // Only what exists for the open document: a submenu with no command left disappears, and so does a separator at
  // either end or next to another (a film's entries in a movie, a movie's in a film).
  const present = (entry: MenuEntry): boolean =>
    typeof entry === "string"
      ? entry === "-" || (commands.has(entry) && !commands.get(entry)!.hidden)
      : entry.children.some((child) => child !== "-" && present(child));
  const shown = entries
    .filter(present)
    .filter(
      (entry, i, all) =>
        entry !== "-" || (i > 0 && i < all.length - 1 && all[i - 1] !== "-"),
    );
  return (
    <>
      {shown.map((entry, i) => {
        if (entry === "-")
          return <Dropdown.Separator key={i} className="command-separator" />;
        if (typeof entry !== "string")
          return (
            <Dropdown.Sub key={entry.label}>
              <Dropdown.SubTrigger className="command-item">
                {entry.label}
                <ChevronRight size={13} />
              </Dropdown.SubTrigger>
              <Dropdown.Portal>
                <Dropdown.SubContent
                  className="command-menu"
                  sideOffset={4}
                  collisionPadding={8}
                >
                  <Items
                    entries={entry.children}
                    target={target}
                    onRun={onRun}
                  />
                </Dropdown.SubContent>
              </Dropdown.Portal>
            </Dropdown.Sub>
          );
        const command = commands.get(entry);
        if (!command) return null;
        const reason = command.reason?.(target);
        return (
          <Dropdown.Item
            key={entry}
            className="command-item"
            aria-disabled={!!reason}
            title={reason}
            onSelect={(e) => {
              if (reason) {
                e.preventDefault();
                return;
              }
              if (onRun) onRun(() => void command.run(target));
              else command.run(target);
            }}
          >
            <span className="command-check">
              {command.checked && <Check size={13} />}
            </span>
            <span>{command.label}</span>
            {command.shortcut && <kbd>{shortcutLabel(command.shortcut)}</kbd>}
            {reason && (
              <span className="command-reason" role="tooltip">
                {reason}
              </span>
            )}
          </Dropdown.Item>
        );
      })}
    </>
  );
}
export function MenuBar() {
  const { commands, target } = useCommands();
  const [open, setOpen] = useState("");
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  // Where the focus was before a menu opened by pointer: a closed menu returns it there rather than to its title, so
  // the next key acts where the user was (usability round 2, C8).
  const before = useRef<HTMLElement | null>(null);
  return (
    <nav className="studio-menubar" aria-label="Application menus">
      {menuNames.map((name, i) => (
        <Dropdown.Root
          key={name}
          modal={false}
          open={open === name}
          onOpenChange={(value) => setOpen(value ? name : "")}
        >
          <Dropdown.Trigger
            ref={(e) => {
              refs.current[i] = e;
            }}
            onPointerEnter={() => {
              if (open) setOpen(name);
            }}
            onPointerDown={() => {
              const active = document.activeElement as HTMLElement | null;
              if (!active?.closest(".studio-menubar")) before.current = active;
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                e.preventDefault();
                const n =
                  (i + (e.key === "ArrowRight" ? 1 : -1) + menuNames.length) %
                  menuNames.length;
                refs.current[n]?.focus();
                if (open) setOpen(menuNames[n]);
              }
            }}
          >
            {name}
          </Dropdown.Trigger>
          <Dropdown.Portal>
            <Dropdown.Content
              className="command-menu"
              align="start"
              sideOffset={2}
              collisionPadding={8}
              onCloseAutoFocus={(e) => {
                const back = before.current;
                before.current = null;
                // Only when the focus would otherwise be lost: a command that placed it (the queue's row, a dialog)
                // keeps it.
                const active = document.activeElement;
                const lost =
                  !active ||
                  active === document.body ||
                  !!active.closest(".studio-menubar, [role=menu]");
                if (
                  lost &&
                  back?.isConnected &&
                  back !== document.body &&
                  !document.querySelector("[role=dialog]")
                ) {
                  e.preventDefault();
                  back.focus({ preventScroll: true });
                }
              }}
            >
              <Items entries={menuEntries(name, commands)} target={target} />
            </Dropdown.Content>
          </Dropdown.Portal>
        </Dropdown.Root>
      ))}
    </nav>
  );
}
export function ContextArea({
  target,
  resolve,
  children,
  className = "",
}: {
  target: Target;
  /** The item under the pointer, for one menu over many items; `target` when nothing resolves. */
  resolve?: (element: HTMLElement) => Target | undefined;
  children: ReactNode;
  className?: string;
}) {
  const { commands, select } = useCommands();
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);
  // The menu acts on the target it opened for, even if the selection moves while it is open.
  const [menuTarget, setMenuTarget] = useState<Target>(target);
  const opener = useRef<HTMLElement | null>(null);
  const pendingAction = useRef<(() => void) | null>(null);
  function open(element: HTMLElement, x: number, y: number) {
    const chosen = resolve?.(element) ?? target;
    opener.current =
      element.closest<HTMLElement>("button,[tabindex]") ?? element;
    if (
      !["chat", "chat-plan", "candidate", "preparation", "producer"].includes(
        chosen.kind,
      )
    )
      select(chosen);
    setMenuTarget(chosen);
    setPoint({ x, y });
  }
  return (
    <div
      className={`context-area ${className}`}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        open(e.target as HTMLElement, e.clientX, e.clientY);
      }}
      onKeyDown={(e) => {
        if ((e.shiftKey && e.key === "F10") || e.key === "ContextMenu") {
          e.preventDefault();
          e.stopPropagation();
          const r = (e.target as HTMLElement).getBoundingClientRect();
          open(
            e.target as HTMLElement,
            r.x + Math.min(24, r.width / 2),
            r.bottom,
          );
        }
      }}
    >
      {children}
      {point &&
        createPortal(
          <Dropdown.Root
            modal={false}
            open
            onOpenChange={(value) => {
              if (!value) setPoint(null);
            }}
          >
            <Dropdown.Trigger
              aria-label="Item actions"
              className="context-anchor"
              style={{ left: point.x, top: point.y }}
            />
            <Dropdown.Portal>
              <Dropdown.Content
                className="command-menu"
                align="start"
                collisionPadding={8}
                onCloseAutoFocus={(e) => {
                  e.preventDefault();
                  opener.current?.focus({ preventScroll: true });
                  // Open a following dialog after the menu has returned focus,
                  // so it captures the persistent track item as its opener.
                  const action = pendingAction.current;
                  pendingAction.current = null;
                  action?.();
                }}
              >
                <Items
                  entries={menuEntries(menuTarget.kind, commands)}
                  target={menuTarget}
                  onRun={(run) => {
                    pendingAction.current = run;
                  }}
                />
              </Dropdown.Content>
            </Dropdown.Portal>
          </Dropdown.Root>,
          document.body,
        )}
    </div>
  );
}
export function CommandButton({
  id,
  children,
  className,
  target: override,
  title,
}: {
  id: string;
  children?: ReactNode;
  className?: string;
  target?: Target;
  /** What the button does, for its tooltip; the shortcut is added. A disabled button shows its reason instead. */
  title?: string;
}) {
  const { commands, target: selected } = useCommands();
  const target = override ?? selected;
  const c = commands.get(id);
  if (!c) return null;
  const reason = c.reason?.(target);
  return (
    <button
      type="button"
      className={className}
      disabled={!!reason}
      aria-pressed={c.checked}
      title={
        reason ??
        (title
          ? `${title}${c.shortcut ? ` (${shortcutLabel(c.shortcut)})` : ""}`
          : c.shortcut
            ? shortcutLabel(c.shortcut)
            : undefined)
      }
      onClick={() => c.run(target)}
    >
      {children ?? c.label}
    </button>
  );
}

export function WorkspaceMenu() {
  const { target } = useCommands();
  return (
    <Dropdown.Root modal={false}>
      <Dropdown.Trigger className="workspace-layout-menu">
        Workspace <ChevronRight size={13} />
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          className="command-menu"
          align="end"
          collisionPadding={8}
        >
          <Items
            entries={["layout.default", "layout.full", "-", "layout.reset"]}
            target={target}
          />
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
