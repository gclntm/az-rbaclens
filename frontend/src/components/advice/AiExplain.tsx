import { useState } from "react";

import { linkButtonClass } from "@/components/chips";
import Button from "@/components/Button";
import { CAPTION, cx } from "@/components/classes";
import { api, type AiPreview, type AiTarget } from "@/api/client";
import { useAiAvailable } from "@/hooks/useAiStatus";

/**
 * "Explain in plain words": Claude rewrites the advice above for a non-specialist.
 * Only shown when AI wording is on (Settings › Advice). The rule text stays the reference.
 */
export default function AiExplain({ target }: { target: AiTarget }) {
  const available = useAiAvailable();
  const [text, setText] = useState<{ text: string; model: string; cached: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<AiPreview | null>(null);
  const [showSent, setShowSent] = useState(false);

  if (!available) return null;

  async function explain() {
    setBusy(true);
    setError(null);
    try {
      setText(await api.ai.explain(target));
    } catch (e) {
      setError(detail((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function toggleSent() {
    if (!showSent && !sent) {
      try {
        setSent(await api.ai.preview(target));
      } catch (e) {
        setError(detail((e as Error).message));
        return;
      }
    }
    setShowSent(!showSent);
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 print:hidden!">
      {text ? (
        <div className="flex-[1_1_100%] rounded-[4px] bg-(--bg-input) px-4 py-2">
          <p className="mx-0 mt-0 mb-1">{text.text}</p>
          <p className={cx(CAPTION, "text-quaternary", "mx-0 mt-0 mb-1")}>
            AI wording ({text.model}
            {text.cached ? ", saved earlier" : ""}). The rule text is the reference; this only puts it in other words.
          </p>
        </div>
      ) : (
        <Button size="sm" type="button" onClick={explain} disabled={busy}>
          {busy ? "Asking Claude…" : "Explain in plain words"}
        </Button>
      )}
      <button type="button" className={linkButtonClass(true)} onClick={toggleSent} aria-expanded={showSent}>
        {showSent ? "Hide what is sent" : "What is sent"}
      </button>
      {error && <p className={cx("my-[1em]", CAPTION, "text-error-primary")}>{error}</p>}
      {showSent && sent && (
        <div className="min-w-0 flex-[1_1_100%]">
          <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
            Sent to {sent.model} with an instruction to use only these facts. No name, UPN or object id leaves RBAC
            Lens.
          </p>
          <pre className="mx-0 mt-1 mb-0 max-h-[280px] overflow-auto rounded-[4px] border border-secondary bg-(--bg-input) p-2 text-[0.75rem] whitespace-pre-wrap [overflow-wrap:anywhere]">{JSON.stringify(sent.facts, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

/** "502 Bad Gateway — {"detail":"…"}" → "…". */
function detail(message: string): string {
  const m = message.match(/"detail":"([^"]+)"/);
  return m ? m[1] : message;
}
