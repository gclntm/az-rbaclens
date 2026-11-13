import { type RefObject, useEffect } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The elements Tab can reach inside ``root``, in order. */
export function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.closest("[hidden]"));
}

/**
 * Focus for a drawer or dialog: while ``active``, focus moves into ``ref``
 * (its first control, or the panel itself), Escape calls ``onClose``, and with
 * ``trap`` Tab cycles inside. When it closes, focus goes back to where it was,
 * so a keyboard user carries on from the row they opened.
 */
export function useDialogFocus(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  onClose: () => void,
  { trap = true }: { trap?: boolean } = {},
) {
  useEffect(() => {
    if (!active) return;
    const before = document.activeElement as HTMLElement | null;
    const root = ref.current;
    if (root && !root.contains(document.activeElement)) {
      (focusables(root)[0] ?? root).focus();
    }
    return () => {
      if (before?.isConnected) before.focus();
    };
    // Focus moves in once per opening, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      // Another dialog over this one has the keyboard: leave the keys to it.
      const owner = (document.activeElement as HTMLElement | null)?.closest('[role="dialog"], [role="alertdialog"]');
      if (owner && owner !== ref.current) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      const root = ref.current;
      if (!trap || e.key !== "Tab" || !root) return;
      const items = focusables(root);
      if (items.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const inside = root.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, onClose, ref, trap]);
}
