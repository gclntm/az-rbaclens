import { AlertCircle, CheckCircle, InfoCircle, XClose } from "@untitledui/icons";
import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";

import { FeaturedIcon } from "@/ui/untitled/components/foundations/featured-icon/featured-icon";

type ToastVariant = "success" | "error" | "info";

/** Each kind's icon and colour (Untitled UI's notification look, phase 3). */
const LOOK = {
  success: { icon: CheckCircle, color: "success" },
  error: { icon: AlertCircle, color: "error" },
  info: { icon: InfoCircle, color: "brand" },
} as const;

/** A button on the toast, e.g. Undo. */
export type ToastAction = { label: string; run: () => void };

type Toast = {
  id: number;
  message: string;
  variant: ToastVariant;
  action?: ToastAction;
};

type ToastContextValue = {
  showToast: (message: string, variant?: ToastVariant, action?: ToastAction) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((ts) => ts.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback(
    (message: string, variant: ToastVariant = "info", action?: ToastAction) => {
      const id = ++idRef.current;
      setToasts((ts) => [...ts, { id, message, variant, action }]);
      // An error stays until dismissed: it may need reading twice, or copying.
      if (variant === "error") return;
      window.setTimeout(() => dismiss(id), action ? 10_000 : 4000);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div
        className="fixed right-4 bottom-4 z-1000 flex w-[min(420px,calc(100vw-2rem))] flex-col gap-2 print:hidden!"
        aria-live="polite"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className="uui flex items-start gap-3 rounded-xl bg-primary p-4 shadow-lg ring-1 ring-secondary duration-200 animate-in fade-in slide-in-from-bottom-2 motion-reduce:animate-none"
            role={t.variant === "error" ? "alert" : "status"}
          >
            <FeaturedIcon icon={LOOK[t.variant].icon} color={LOOK[t.variant].color} theme="light" size="sm" />
            <span className="min-w-0 flex-1 self-center text-sm font-medium text-primary [overflow-wrap:anywhere] select-text">
              {t.message}
            </span>
            {t.action && (
              <button
                type="button"
                className="flex-none cursor-pointer self-center rounded-md text-sm font-semibold text-brand-secondary outline-focus-ring hover:text-brand-secondary_hover hover:underline focus-visible:outline-2 focus-visible:outline-offset-2"
                onClick={(e) => {
                  e.stopPropagation();
                  t.action?.run();
                  dismiss(t.id);
                }}
              >
                {t.action.label}
              </button>
            )}
            <button
              type="button"
              className="-m-1 flex size-8 flex-none cursor-pointer items-center justify-center rounded-md text-fg-quaternary outline-focus-ring hover:bg-primary_hover hover:text-fg-quaternary_hover focus-visible:outline-2"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
            >
              <XClose aria-hidden="true" className="size-5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used inside <ToastProvider>");
  }
  return ctx;
}

/** Wrap an async operation so success / failure toasts fire automatically. */
export function useAsyncToast() {
  const { showToast } = useToast();
  return useCallback(
    async <T,>(
      op: () => Promise<T>,
      successMessage?: string,
      errorPrefix = "Error",
    ): Promise<T | null> => {
      try {
        const result = await op();
        if (successMessage) showToast(successMessage, "success");
        return result;
      } catch (e) {
        showToast(`${errorPrefix}: ${(e as Error).message}`, "error");
        return null;
      }
    },
    [showToast],
  );
}

