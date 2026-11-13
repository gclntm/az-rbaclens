import { useEffect, useState, type FormEvent } from "react";

import Button, { buttonClass } from "@/components/Button";
import { api, type NotificationKind, type NotificationStatus, type OutboxNotification } from "@/api/client";
import { BADGE, BADGE_LEVEL, BADGE_TONE, CAPTION, EMPTY_STATE, cx } from "@/components/classes";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";
import { formatDateTime } from "@/pages/scopes/scopeUtils";

import {
  FEED,
  FEED_ACTIONS,
  FEED_ITEM,
  FEED_MAIN,
  FEED_META,
  FEED_TITLE,
  FIELD_FORM,
  FIELD_FORM_FIELD,
  FIELD_FORM_INPUT,
  TAB_STACK,
} from "./ResponsibleTab";

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");

const KIND_LABEL: Record<NotificationKind, string> = {
  ask: "Question",
  review_opened: "Review opened",
  review_due: "Review due soon",
  reminder: "Reminder",
  todo_overdue: "To-dos overdue",
  data_stale: "Data not up to date",
};

/** What a notification would say, in one line. */
export function notificationText(n: Pick<OutboxNotification, "kind" | "payload">): string {
  const p = n.payload as Record<string, string | number | boolean | null | undefined>;
  switch (n.kind) {
    case "ask":
      return p.to_the_person
        ? `Do you still need ${p.role ?? "this role"} on ${p.place}?${p.note ? ` "${p.note}"` : ""}`
        : `Question about ${p.who} (${p.role ?? "a role"} on ${p.place})${p.note ? `: "${p.note}"` : ""}`;
    case "review_opened":
      return p.place ? `Your review of ${p.place} for ${p.period} is open.` : `The admins' review for ${p.period} is open.`;
    case "review_due":
      return `${p.left} still to decide in the review for ${p.period}; it is due soon.`;
    case "reminder":
      return `Still open: the question about ${p.who} (${p.role ?? "a role"} on ${p.place}).`;
    case "todo_overdue":
      return `${p.count} change(s) from reviews still to make in Azure, open for more than 14 days.`;
    case "data_stale":
      return `The data is not up to date: ${p.count} problem(s), e.g. a schedule off or behind.`;
  }
}

/** Settings › Notifications: the outbox, until email or Teams is connected. */
export default function NotificationsTab() {
  const { showToast } = useToast();
  const [status, setStatus] = useState<NotificationStatus | null>(null);
  const [rows, setRows] = useState<OutboxNotification[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    Promise.all([api.notifications.status(), api.notifications.list(50)])
      .then(([s, r]) => {
        setStatus(s);
        setRows(r);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function sendTest(e: FormEvent) {
    e.preventDefault();
    setSending(true);
    try {
      const r = await api.notifications.test(to.trim());
      showToast(`Test message sent to ${r.sent_to}`, "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className={TAB_STACK}>
      <SectionCard
        icon="reviews"
        title="Notifications"
        actions={
          <>
            {status && (
              <span className={cx(BADGE, status.connected ? BADGE_TONE.done : BADGE_TONE.pending)}>
                {!status.connected ? "Not connected yet" : status.channel === "smtp" ? "Captured in Mailpit" : "Email connected"}
              </span>
            )}
          </>
        }
      >
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          RBAC Lens sends a message when a reviewer asks someone about their access, when a monthly review opens or
          is due soon, and as a reminder for open questions. Messages wait here until they are sent.
        </p>
        {status && (
          <p className={cx("my-[1em]", CAPTION)}>
            {status.pending} waiting · {status.sent} sent
            {status.skipped > 0 && ` · ${status.skipped} not sent (no address, or too old)`}
            {Object.entries(status.by_kind).length > 0 &&
              ` · waiting: ${Object.entries(status.by_kind)
                .map(([k, n]) => `${n} ${KIND_LABEL[k as NotificationKind].toLowerCase()}`)
                .join(", ")}`}
          </p>
        )}
        {status?.failed_last && <p className={cx("my-[1em]", CAPTION, "text-error-primary")}>Last failure: {status.failed_last}</p>}
      </SectionCard>

      {status?.connected ? (
        <SectionCard
          icon="reviews"
          title={status.channel === "smtp" ? "Email, captured in Mailpit" : "Email through Microsoft Graph"}
          actions={
            <>
              {status.inbox_url && (
                <a className={buttonClass("secondary", "sm")} href={status.inbox_url} target="_blank" rel="noreferrer">
                  Open the inbox
                </a>
              )}
            </>
          }
        >
          {status.channel === "smtp" && (
            <p className={cx("my-[1em]", CAPTION_MUTED)}>
              Messages are kept in Mailpit&apos;s inbox and never reach anyone. To send them for real, set{" "}
              <code>NOTIFICATION_CHANNEL=email</code> (see the steps in docs/deployment.md).
            </p>
          )}
          <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-0.5 text-[0.8125rem]">
            <dt className="text-tertiary">Sent from</dt>
            <dd className="m-0">{status.sender}</dd>
            <dt className="text-tertiary">Admins&apos; review</dt>
            <dd className="m-0">{status.admin_email ?? <span className="text-error-primary">no address: those messages are not sent</span>}</dd>
            <dt className="text-tertiary">Links</dt>
            <dd className="m-0">{status.links ? "messages link to RBAC Lens" : "no PUBLIC_URL: messages carry no link"}</dd>
          </dl>
          <form className={FIELD_FORM} onSubmit={sendTest} style={{ marginTop: "var(--space-md)" }}>
            <label className={FIELD_FORM_FIELD}>
              <span>Send a test message to</span>
              <input type="email" className={cx("tracking-normal", FIELD_FORM_INPUT)} value={to} onChange={(e) => setTo(e.target.value)} placeholder="you@example.org" maxLength={320} />
            </label>
            <Button size="sm" type="submit" disabled={sending || !to.trim()}>
              {sending ? "Sending…" : "Send test"}
            </Button>
          </form>
        </SectionCard>
      ) : (
        status && (
          <SectionCard
            icon="settings"
            title="How to connect email"
          >
            <ol className={cx(CAPTION, "m-0 flex list-decimal flex-col gap-1 pl-5 leading-[1.5] [&_code]:[overflow-wrap:anywhere]")}>
              <li>
                In Entra ID, give the app registration RBAC Lens uses the Microsoft Graph application permission{" "}
                <strong>Mail.Send</strong>, and grant admin consent.
              </li>
              <li>
                Limit it to one mailbox (recommended): in Exchange Online PowerShell,{" "}
                <code>New-ApplicationAccessPolicy -AppId &lt;client id&gt; -PolicyScopeGroupId rbaclens@yourdomain
                -AccessRight RestrictAccess</code>.
              </li>
              <li>
                In <code>.env.prod</code>: <code>NOTIFICATION_CHANNEL=email</code>,{" "}
                <code>NOTIFICATION_SENDER=rbaclens@yourdomain</code>, <code>NOTIFICATION_ADMIN_EMAIL=</code> (who gets
                the admins&apos; messages) and <code>PUBLIC_URL=</code> (the app&apos;s address, for links).
              </li>
              <li>
                Redeploy (<code>make up prod</code>), then send a test message from this page. Messages waiting for more
                than 7 days are closed rather than sent late.
              </li>
            </ol>
          </SectionCard>
        )
      )}

      {error && <p className="my-[1em] text-error-primary">Could not load: {error}</p>}
      {rows && (
        <SectionCard
          icon="history"
          title="Latest"
        >
          {rows.length === 0 ? (
            <p className={cx("my-[1em]", EMPTY_STATE)}>Nothing yet. Messages appear when reviews open and questions are asked.</p>
          ) : (
            <ul className={FEED}>
              {rows.map((n) => (
                <li key={n.id} className={FEED_ITEM}>
                  <span className={cx(BADGE, BADGE_LEVEL.resource)}>{KIND_LABEL[n.kind]}</span>
                  <div className={FEED_MAIN}>
                    <span className={FEED_TITLE}>{notificationText(n)}</span>
                    <span className={FEED_META}>
                      to {n.recipient_name ?? n.recipient_id ?? "the admins"} · {formatDateTime(n.created_at)} ·{" "}
                      {n.sent_at ? `sent by ${n.channel}` : n.error ? `failed: ${n.error}` : "waiting"}
                    </span>
                  </div>
                  {typeof n.payload.path === "string" && (
                    <div className={FEED_ACTIONS}>
                      <Button size="sm" to={n.payload.path}>
                        Open
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      )}
    </section>
  );
}
