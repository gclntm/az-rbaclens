import { useEffect, useState } from "react";

import Button from "@/components/Button";
import { api, type AiSettingsRead } from "@/api/client";
import { CAPTION, FORM_FIELD, FORM_ROW, cx } from "@/components/classes";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";
import { forgetAiStatus } from "@/hooks/useAiStatus";

const MODEL_LABEL: Record<string, string> = {
  "claude-opus-5-5": "Claude Opus 5.5 (best wording)",
  "claude-sonnet-5-5": "Claude Sonnet 5.5",
  "claude-haiku-5-5": "Claude Haiku 5.5 (cheapest)",
};

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");

/** A number or key field on the Advice settings (the fields stay native). */
export const ADVICE_INPUT =
  "min-h-10 w-[140px] rounded-[4px] border border-secondary bg-(--bg-input) px-3 py-2 text-[0.875rem] text-primary " +
  "[font-family:inherit] [font-style:inherit] [font-weight:inherit] [line-height:inherit] " +
  "focus:border-(--border-focus) focus:outline-none";

/** Settings › Advice: optional AI wording. Off by default; the key is stored encrypted and never shown. */
export default function AiSettingsCard() {
  const { showToast } = useToast();
  const [s, setS] = useState<AiSettingsRead | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [model, setModel] = useState("claude-opus-5-5");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function apply(r: AiSettingsRead) {
    setS(r);
    setEnabled(r.enabled);
    setModel(r.model);
    setKey("");
  }

  useEffect(() => {
    api.ai
      .settings()
      .then(apply)
      .catch((e: Error) => setError(e.message));
  }, []);

  async function save(apiKey: string | null) {
    setBusy(true);
    try {
      apply(await api.ai.saveSettings({ enabled, model, api_key: apiKey }));
      forgetAiStatus();
      showToast(apiKey === "" ? "API key removed" : "AI wording saved", "success");
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="my-[1em] text-error-primary">Could not load the AI settings: {error}</p>;
  if (!s) return null;

  const source =
    s.key_source === "stored"
      ? "An API key is stored, encrypted."
      : s.key_source === "environment"
        ? "Using ANTHROPIC_API_KEY from the server's environment."
        : "No API key yet.";

  return (
    <SectionCard
      className="mt-4"
      icon="todo"
      title="AI wording (optional)"
      hint="When on, grant and identity pages offer “Explain in plain words”: Claude rewrites the advice for someone who is not a cloud specialist. The rules still decide every action; the report never uses AI."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save(key.trim() ? key.trim() : null);
        }}
      >
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          <strong>What is sent:</strong> the role, the place, what the role allows, how it is given, day counts and the
          advice&apos;s own text. Never a person&apos;s name, UPN or object id: the identity becomes &quot;a person&quot;,
          &quot;a group&quot; or &quot;an app&quot;. Each page shows the exact facts under &quot;What is sent&quot;.
          Texts are kept, so the same facts are never sent twice.
        </p>
        <p className={cx("my-[1em]", CAPTION)}>
          <strong>This leaves your tenant:</strong> role names, including your custom roles, and place names (management
          groups, subscriptions, resource groups, resources) go to Anthropic&apos;s API with each request. Turn this on
          only if that is allowed where you work.
        </p>
        {(s.daily_limit ?? 0) > 0 && (
          <p className={cx("my-[1em]", CAPTION_MUTED)}>
            At most {s.daily_limit} new texts a day (AI_DAILY_LIMIT on the server); {s.written_today ?? 0} written today.
            Texts already written still show once the limit is reached.
          </p>
        )}

        <label className="mx-0 my-2 flex items-center gap-2 font-medium">
          <input className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] mt-[3px] mr-[3px] mb-[3px] ml-1" type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Turn on AI wording
        </label>

        <div className={FORM_ROW}>
          <label className={FORM_FIELD}>
            <span>Model</span>
            <select className="font-normal tracking-normal" value={model} onChange={(e) => setModel(e.target.value)}>
              {s.models.map((m) => (
                <option key={m} value={m}>
                  {MODEL_LABEL[m] ?? m}
                </option>
              ))}
            </select>
          </label>
          <label className={cx(FORM_FIELD, "min-w-0 flex-[1_1_260px]")}>
            <span>Anthropic API key</span>
            <input
              type="password"
              className={cx("tracking-normal", ADVICE_INPUT, "w-full")}
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={s.key_source === "stored" ? "Stored: leave empty to keep it" : "sk-ant-…"}
              disabled={!s.encryption_available}
            />
          </label>
        </div>
        <p className={cx("my-[1em]", CAPTION)}>
          {source}
          {!s.encryption_available && " Set ENCRYPTION_KEY on the server to store a key here."}
        </p>

        <div className={FORM_ROW}>
          <Button variant="primary" size="sm" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </Button>
          {s.key_source === "stored" && (
            <Button size="sm" type="button" onClick={() => void save("")} disabled={busy}>
              Remove the stored key
            </Button>
          )}
        </div>
      </form>
    </SectionCard>
  );
}
