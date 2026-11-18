import type { CSSProperties, FC, MouseEvent, ReactNode } from "react";

import { Button as UntitledButton, styles } from "@/ui/untitled/components/base/buttons/button";
import { cx } from "@/components/classes";

/**
 * The app's button (docs/design.md): Untitled UI's button
 * with the props our pages already use.
 *
 * - ``disabled`` maps to React Aria's ``isDisabled``.
 * - ``to`` makes it a link inside the app (React Aria's RouterProvider, App.tsx).
 * - ``title``: React Aria drops it from the button, so it sits on a wrapping span.
 *   That also shows it on a disabled button, where it usually says why.
 * - Hidden in print, as the old .btn was (print.css).
 *
 * React Aria stops a click from reaching parent elements and document
 * listeners; nothing in the app relies on that today.
 */
type Common = {
  /** ``danger``: an action that deletes or loses something (red). */
  variant?: "primary" | "secondary" | "tertiary" | "danger";
  size?: "xs" | "sm" | "md";
  title?: string;
  iconLeading?: FC<{ className?: string }>;
  children: ReactNode;
  "aria-label"?: string;
};

type AsButton = Common & {
  to?: undefined;
  type: "button" | "submit" | "reset";
  /** React Aria types the target as any focusable element, not only a button. */
  onClick?: (e: MouseEvent<Element>) => void;
  disabled?: boolean;
  form?: string;
  style?: CSSProperties;
  "aria-expanded"?: boolean;
  "aria-haspopup"?: "menu" | "dialog" | "listbox" | boolean;
  "aria-pressed"?: boolean;
};

type AsLink = Common & { to: string };

export default function Button(props: AsButton | AsLink) {
  const { variant = "secondary", size = "md", title, iconLeading, children } = props;
  const color: "primary" | "secondary" | "tertiary" | "primary-destructive" =
    variant === "danger" ? "primary-destructive" : variant;
  const common = { color, size, iconLeading, "aria-label": props["aria-label"], className: "print:hidden" };
  const button =
    props.to !== undefined ? (
      <UntitledButton {...common} href={props.to}>
        {children}
      </UntitledButton>
    ) : (
      <UntitledButton
        {...common}
        type={props.type}
        onClick={props.onClick}
        isDisabled={props.disabled}
        form={props.form}
        style={props.style}
        aria-expanded={props["aria-expanded"]}
        aria-haspopup={props["aria-haspopup"]}
        aria-pressed={props["aria-pressed"]}
      >
        {children}
      </UntitledButton>
    );
  if (!title) return button;
  return (
    <span className="inline-flex print:hidden" title={title}>
      {button}
    </span>
  );
}

/**
 * Untitled UI's button look for an element that must stay a plain <a> (a download
 * through the API client, a link that opens a new tab). Not a .uui region, so it
 * also clears the old link underline.
 */
export function buttonClass(variant: "primary" | "secondary" | "tertiary" = "secondary", size: "xs" | "sm" | "md" = "sm") {
  return cx(
    styles.common.root,
    styles.sizes[size].root,
    styles.colors[variant].root,
    "no-underline hover:no-underline print:hidden",
  );
}
