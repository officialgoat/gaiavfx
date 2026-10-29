import {
  useLayoutEffect,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as Dialog from "@radix-ui/react-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { X, MoreHorizontal } from "lucide-react";

export function ActionMenu({
  label,
  items,
  className = "",
}: {
  label: string;
  className?: string;
  items: { label: string; disabled?: boolean; run: () => void }[];
}) {
  const trigger = useRef<HTMLButtonElement | null>(null);
  const selectedAction = useRef<(() => void) | null>(null);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          ref={trigger}
          className={`icon-button ${className}`}
          aria-label={label}
        >
          <MoreHorizontal size={16} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="action-menu"
          align="end"
          sideOffset={5}
          onCloseAutoFocus={(event) => {
            const action = selectedAction.current;
            selectedAction.current = null;
            if (action) {
              event.preventDefault();
              // A following dialog must capture the persistent menu trigger,
              // not the menu item that is being removed.
              trigger.current?.focus();
              action();
            }
          }}
        >
          {items.map((item) => (
            <DropdownMenu.Item
              key={item.label}
              disabled={item.disabled}
              onSelect={() => {
                selectedAction.current = item.run;
              }}
            >
              {item.label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function IconButton({
  label,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  children: ReactNode;
}) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          type="button"
          className="icon-button"
          aria-label={label}
          {...props}
        >
          {children}
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="tooltip" sideOffset={7}>
          {label}
          <Tooltip.Arrow />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function Sheet({
  title,
  description,
  open,
  onOpenChange,
  returnFocusTo,
  children,
  className = "",
}: {
  title: string;
  description: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnFocusTo?: React.RefObject<HTMLElement | null>;
  children: ReactNode;
  className?: string;
}) {
  const opener = useRef<HTMLElement | null>(null);
  const menuTrigger = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    opener.current = document.activeElement as HTMLElement | null;
    // A menu item that opens a sheet is gone when the sheet closes: its menu's trigger (Radix labels a menu by it)
    // takes focus back instead.
    const label = opener.current
      ?.closest('[role="menu"]')
      ?.getAttribute("aria-labelledby");
    menuTrigger.current = label ? document.getElementById(label) : null;
  }, [open]);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content
          className={`sheet ${className}`}
          aria-label={title}
          aria-labelledby={undefined}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = [
              returnFocusTo?.current,
              opener.current,
              menuTrigger.current,
              // A context menu has no trigger button: right-click selected the item it acted on.
              document.querySelector<HTMLElement>(
                '.track-item[aria-pressed="true"]',
              ),
            ].find((element) => element?.isConnected);
            target?.focus();
          }}
        >
          <div className="sheet-heading">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close asChild>
              <button className="icon-button" aria-label="Close panel">
                <X size={18} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="muted">
            {description}
          </Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function SideDrawer({
  title,
  side,
  onClose,
  children,
}: {
  title: string;
  side: "left" | "right";
  onClose: () => void;
  children: ReactNode;
}) {
  const opener = useRef(document.activeElement as HTMLElement | null);
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content
          className="panel-drawer"
          aria-label={title}
          aria-labelledby={undefined}
          data-side={side}
          aria-describedby={undefined}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            opener.current?.focus();
          }}
        >
          <Dialog.Title className="sr-only">{title}</Dialog.Title>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
