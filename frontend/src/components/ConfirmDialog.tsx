import { AlertTriangle } from "@untitledui/icons";
import { createContext, type ReactNode, useCallback, useContext, useId, useRef, useState } from "react";

import Button from "@/components/Button";
import { useDialogFocus } from "@/hooks/useDialogFocus";
import { FeaturedIcon } from "@/ui/untitled/components/foundations/featured-icon/featured-icon";

/**
 * "Are you sure?" in Untitled UI's modal look, instead of the browser's confirm()
 * (docs/design.md). ``await confirm({...})`` resolves to true
 * on the action button, false on Cancel, Escape or a click beside the dialog.
 * Focus moves in (to Cancel, the safe choice) and returns afterwards.
 */
export type ConfirmRequest = {
  title: string;
  body?: ReactNode;
  /** The action button's words, e.g. "Delete". */
  action: string;
  /** Deletes or loses something: red button and icon. */
  danger?: boolean;
};

type Ask = (request: ConfirmRequest) => Promise<boolean>;

// Outside the provider (a test rendering one page), the browser's own confirm() stands in.
const ConfirmContext = createContext<Ask>((request) => Promise.resolve(window.confirm(request.title)));

export function useConfirm(): Ask {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<(ConfirmRequest & { resolve: (ok: boolean) => void }) | null>(null);
  const ask = useCallback<Ask>((request) => new Promise((resolve) => setOpen({ ...request, resolve })), []);
  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      {open && (
        <ConfirmDialog
          request={open}
          onDone={(ok) => {
            open.resolve(ok);
            setOpen(null);
          }}
        />
      )}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({ request, onDone }: { request: ConfirmRequest; onDone: (ok: boolean) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const bodyId = useId();
  useDialogFocus(ref, true, () => onDone(false));
  return (
    // A click beside the dialog cancels it, as Escape does from the keyboard.
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div
      className="uui fixed inset-0 z-60 flex items-end justify-center bg-overlay/70 px-4 pb-4 backdrop-blur-[6px] duration-300 animate-in fade-in motion-reduce:animate-none sm:items-center"
      onClick={(e) => e.target === e.currentTarget && onDone(false)}
    >
      <div
        ref={ref}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={request.body ? bodyId : undefined}
        tabIndex={-1}
        className="w-full max-w-100 rounded-2xl bg-primary p-6 shadow-xl outline-hidden duration-300 animate-in zoom-in-95 motion-reduce:animate-none"
      >
        <FeaturedIcon icon={AlertTriangle} color={request.danger ? "error" : "warning"} theme="light" size="lg" />
        <h2 id={titleId} className="mt-4 text-lg font-semibold text-primary">
          {request.title}
        </h2>
        {request.body && (
          <div id={bodyId} className="mt-1 text-sm text-tertiary">
            {request.body}
          </div>
        )}
        <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <Button type="button" onClick={() => onDone(false)}>
            Cancel
          </Button>
          <Button type="button" variant={request.danger ? "danger" : "primary"} onClick={() => onDone(true)}>
            {request.action}
          </Button>
        </div>
      </div>
    </div>
  );
}
