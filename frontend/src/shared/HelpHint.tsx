import * as Tooltip from "@radix-ui/react-tooltip";
import { CircleHelp } from "lucide-react";
import { useState } from "react";
import "./help-hint.css";

export function HelpHint({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Tooltip.Provider delayDuration={250}>
      <Tooltip.Root open={open} onOpenChange={setOpen}>
        <Tooltip.Trigger asChild>
          <button
            type="button"
            className="help-hint"
            aria-label={`Help: ${label}`}
            aria-expanded={open}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setOpen(true);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setOpen(false);
              }
            }}
          >
            <CircleHelp size={14} />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="tooltip help-hint-content"
            sideOffset={6}
            collisionPadding={12}
          >
            {children}
            <Tooltip.Arrow />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
