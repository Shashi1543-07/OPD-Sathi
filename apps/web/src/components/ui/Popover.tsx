import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

type Placement = { top: number; left: number; maxHeight: number };

const GAP = 6;
const EDGE = 8;

/**
 * A button that opens a floating panel above everything else on the page.
 *
 * The panel is rendered at the end of <body> with fixed positioning, so it is never cut
 * off by a scrolling list (the queue's virtual list clipped in-row pop-overs). It opens
 * below the button, or above when there is more room there, stays inside the window,
 * follows the button while lists scroll, and closes on Escape or a click elsewhere.
 */
export function Popover({
  label,
  ariaLabel,
  triggerClassName,
  panelClassName,
  align = "start",
  children,
}: {
  label: ReactNode;
  ariaLabel?: string;
  triggerClassName?: string;
  panelClassName?: string;
  /** Line the panel up with the button's left ("start") or right ("end") edge. */
  align?: "start" | "end";
  children: ReactNode | ((close: () => void) => ReactNode);
}) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<Placement>();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();

  const [restoreFocus, setRestoreFocus] = useState(false);
  const close = useCallback((focusTrigger = false) => {
    setOpen(false);
    setPlacement(undefined);
    setRestoreFocus(focusTrigger);
  }, []);

  // Return keyboard focus to the button after Escape or an action inside the panel.
  useEffect(() => {
    if (!open && restoreFocus) trigger.current?.focus();
  }, [open, restoreFocus]);

  const place = useCallback(() => {
    const button = trigger.current;
    const box = panel.current;
    if (!button || !box) return;
    const anchor = button.getBoundingClientRect();
    if (anchor.bottom < 0 || anchor.top > window.innerHeight) {
      close();
      return;
    }
    const width = box.offsetWidth;
    const height = box.scrollHeight;
    const below = window.innerHeight - anchor.bottom - GAP - EDGE;
    const above = anchor.top - GAP - EDGE;
    const upward = height > below && above > below;
    const maxHeight = Math.max(120, upward ? above : below);
    const shown = Math.min(height, maxHeight);
    const preferredLeft = align === "end" ? anchor.right - width : anchor.left;
    setPlacement({
      top: upward ? anchor.top - GAP - shown : anchor.bottom + GAP,
      left: Math.min(Math.max(EDGE, preferredLeft), window.innerWidth - width - EDGE),
      maxHeight,
    });
  }, [align, close]);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    panel.current?.focus({ preventScroll: true });
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panel.current?.contains(target) && !trigger.current?.contains(target)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(true);
    };
    const onMove = () => place();
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [open, place, close]);

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={triggerClassName}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-haspopup="dialog"
        onClick={() => {
          if (open) {
            close();
            return;
          }
          setRestoreFocus(false);
          setOpen(true);
        }}
      >
        {label}
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            id={id}
            role="dialog"
            aria-label={ariaLabel}
            tabIndex={-1}
            className={`ui-popover ${panelClassName ?? ""}`.trim()}
            style={{
              top: placement?.top ?? 0,
              left: placement?.left ?? 0,
              maxHeight: placement?.maxHeight,
              visibility: placement ? "visible" : "hidden",
            }}
          >
            {typeof children === "function" ? children(() => close(true)) : children}
          </div>,
          document.body,
        )}
    </>
  );
}
