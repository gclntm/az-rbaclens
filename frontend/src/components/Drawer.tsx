import { type ReactNode, useId, useRef } from "react";

import { cx } from "@/components/classes";
import CloseButton from "@/components/CloseButton";
import { useDialogFocus } from "@/hooks/useDialogFocus";

/** Facts at the top of a drawer: a <dl> of <div><dt/><dd/></div>, terms and values in two columns. */
export const DRAWER_META =
  "m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 [&>div]:contents " +
  "[&_dt]:text-[0.8125rem] [&_dt]:font-semibold [&_dt]:text-tertiary " +
  "[&_dd]:m-0 [&_dd]:overflow-hidden [&_dd]:text-ellipsis [&_dd]:whitespace-nowrap";
/** A part of a drawer's body, with a line above, and its title. */
export const DRAWER_SECTION = "border-t border-t-secondary pt-2";
export const DRAWER_SECTION_TITLE = "mx-0 mt-0 mb-1 text-[0.9rem] font-semibold";

/**
 * A panel over the page, from the right: a title, a close button, then the
 * body. Focus moves in and stays in until it closes (Escape, ×, or a click
 * beside it), then returns to what opened it.
 *
 * The shell has Untitled UI's slide-out look (overlay, panel, header); the
 * focus handling stays ours (useDialogFocus, tested). The body holds pages'
 * own markup, so the panel is not a .uui region.
 */
export default function Drawer({
  title,
  onClose,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  useDialogFocus(ref, true, onClose);
  return (
    // A click beside the panel closes it, as Escape does from the keyboard.
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div
      className="fixed inset-0 z-50 flex justify-end bg-overlay/70 pl-6 backdrop-blur-[6px] duration-300 animate-in fade-in motion-reduce:animate-none md:pl-10"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <aside
        ref={ref}
        className={cx(
          // Not CARD: its card shadow would win over the panel's large shadow and ring.
          "card m-0 flex h-dvh w-full max-w-160 flex-col gap-4 overflow-y-auto rounded-none border-0 bg-primary px-6 pt-0 pb-6 shadow-xl ring-1 ring-secondary outline-hidden duration-300 animate-in slide-in-from-right motion-reduce:animate-none",
          // In print the old .card rule gave the panel a 1px grey border, white background, no shadow.
          "print:break-inside-avoid print:border! print:border-[#888]! print:bg-white! print:[box-shadow:none]!",
        )}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="sticky top-0 z-1 -mx-6 flex items-center gap-3 border-b border-secondary bg-primary py-3 pr-3 pl-6">
          <h3 className="m-0 min-w-0 flex-1 truncate text-lg font-semibold text-primary" id={titleId}>
            {title}
          </h3>
          <CloseButton onClick={onClose} />
        </header>
        {children}
      </aside>
    </div>
  );
}
