import { useState, type MouseEvent, type ReactNode } from "react";

import { downloadFile } from "@/api/client";
import { buttonClass } from "@/components/Button";
import { useToast } from "@/components/Toast";

/**
 * An export link that works with sign-in on: a plain click downloads through
 * the API client, which sends the token. Ctrl/Cmd-click keeps the browser's
 * own behaviour (works while sign-in is off).
 */
export default function DownloadLink({
  href,
  className = buttonClass("secondary", "sm"),
  title,
  children,
}: {
  href: string;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  const { showToast } = useToast();
  const [busy, setBusy] = useState(false);

  function onClick(e: MouseEvent<HTMLAnchorElement>) {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    downloadFile(href)
      .catch((err: Error) => showToast(`Could not download: ${err.message}`, "error"))
      .finally(() => setBusy(false));
  }

  return (
    <a href={href} className={className} title={title} onClick={onClick} aria-busy={busy}>
      <span className="px-0.5">{busy ? "Downloading…" : children}</span>
    </a>
  );
}
