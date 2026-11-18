import { Link } from "react-router-dom";

import { CAPTION, TABLE_COMPACT, TABLE_SCROLL, cx } from "@/components/classes";
import { NAV_ICONS, type NavIconName } from "@/components/NavIcon";
import { PageHeader, SectionCard } from "@/components/PageTemplate";
import { FeaturedIcon } from "@/ui/untitled/components/foundations/featured-icon/featured-icon";

import HelpLoopDiagram from "./HelpLoopDiagram";

/** The colours a featured icon takes: neutral brand for the steps, a state for decisions. */
type IconColor = "brand" | "success" | "error" | "warning" | "gray";

type Step = { icon: NavIconName; title: string; short: string; text: string; to: string; link: string };

/** The loop: RBAC Lens finds, a person decides, someone acts in Azure, the next check confirms. */
const LOOP: Step[] = [
  {
    icon: "rocket",
    title: "The daily check",
    short: "every night",
    text: "Every night RBAC Lens reads who can do what, from the management groups down to Entra ID.",
    to: "/admin/data",
    link: "Data & schedules",
  },
  {
    icon: "look",
    title: "Needs a look",
    short: "what needs attention",
    text: "It flags the grants that deserve attention: unused, too broad, idle admins, owned apps, old secrets.",
    to: "/findings",
    link: "Needs a look",
  },
  {
    icon: "reviews",
    title: "Reviews",
    short: "once a month, per place",
    text: "Once a month, each person responsible for a place decides on its grants: keep, remove, change or ask.",
    to: "/reviews",
    link: "Reviews",
  },
  {
    icon: "todo",
    title: "To do",
    short: "a command to run in Azure",
    text: "Every remove or change becomes a to-do with the exact command. Someone runs it in Azure.",
    to: "/todos",
    link: "To do",
  },
  {
    icon: "repeat",
    title: "Confirmed",
    short: "by the next check",
    text: "The next check sees the grant gone and ticks the to-do off. The Changes page keeps the proof.",
    to: "/changes",
    link: "Changes",
  },
];

type Role = { icon: NavIconName; who: string; points: string[] };

const ROLES: Role[] = [
  {
    icon: "self",
    who: "You are responsible for a place",
    points: [
      "Open Reviews once a month: your round lists the grants given on your places.",
      "Each grant comes with advice. Check the ones that matter, then accept the rest at once.",
      "Finish the round: removals and changes go to To do on their own.",
    ],
  },
  {
    icon: "shield",
    who: "You are an admin",
    points: [
      "Watch Needs a look for anything high: no need to wait for the monthly review.",
      "Work through To do: copy the command, run it, and let the next check confirm.",
      "Set who is responsible for each place (Settings), so reviews reach the right person.",
    ],
  },
  {
    icon: "people",
    who: "Everyone",
    points: [
      "Find yourself on People & apps: your access, where it comes from, and what you did with it.",
      "Got an email asking whether you still need some access? Answer the reviewer.",
    ],
  },
];

type Decision = { icon: NavIconName; color: IconColor; name: string; text: string };

const DECISIONS: Decision[] = [
  { icon: "todo", color: "success", name: "Keep", text: "It stays. Its findings are marked reviewed; undo the keep and they reopen." },
  { icon: "look", color: "error", name: "Remove", text: "It goes: a to-do with the command to run." },
  { icon: "compass", color: "brand", name: "Change", text: "Keep the access, give it differently: PIM, a smaller role, a shorter secret. Say how in a note." },
  { icon: "bell", color: "warning", name: "Ask", text: "Not sure? Ask the person, or whoever gave it. A reminder follows in 7 days." },
];

const SET_ASIDE: { name: string; meaning: string; lasts: string; advice: string }[] = [
  { name: "Mark reviewed", meaning: "Seen, and in hand: a fix is planned or under way.", lasts: "Until reopened", advice: "Still recommends the fix" },
  { name: "Accept risk", meaning: "On purpose, and it stays. A reason is required: your record for an audit.", lasts: "Until reopened", advice: "Stops recommending the fix" },
  { name: "Snooze 30 d", meaning: "Not now: waiting on someone.", lasts: "30 days, then open again", advice: "Still recommends the fix" },
];

const TIPS: { icon: NavIconName; text: string }[] = [
  { icon: "lock", text: "RBAC Lens never changes Azure. It shows, suggests and checks; people act." },
  { icon: "idea", text: "Azure records changes, never reads. Someone who only looks shows no activity: ask before removing." },
  { icon: "self", text: "Nobody reviews their own access, or an app they own: it goes to the next person up, or to the admins." },
  { icon: "repeat", text: "A grant kept last month and unchanged since starts as kept. Only what is new or flagged needs you." },
  { icon: "terminal", text: "Every command is ready to copy. The next check ticks the to-do off once it is done." },
  { icon: "award", text: "The Report sums it all up against ISO 27001, for an audit or a manager." },
];

/** A newcomer's guide to the workflow. Route: /help. */
export default function HelpPage() {
  return (
    <>
      <PageHeader
        title="How it works"
        sub="RBAC Lens finds, you decide, someone acts in Azure, and the next check confirms. Five minutes to get the idea."
      />

      <SectionCard icon="rocket" title="The loop" hint="Every grant goes round the same circle, at its own pace.">
        <HelpLoopDiagram nodes={LOOP} />
        <ol className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-4 p-0">
          {LOOP.map((s, i) => (
            <li key={s.title} className="flex flex-col gap-2 rounded-xl border border-secondary p-4">
              <div className="flex items-center gap-3">
                <FeaturedIcon icon={NAV_ICONS[s.icon]} color="brand" theme="light" size="md" />
                <span className={cx(CAPTION, "font-semibold")} aria-hidden="true">
                  {i + 1} / {LOOP.length}
                </span>
              </div>
              <h3 className="m-0 text-md font-semibold text-primary">{s.title}</h3>
              <p className="m-0 flex-1 text-sm text-tertiary">{s.text}</p>
              <Link to={s.to} className="text-sm">
                {s.link} →
              </Link>
            </li>
          ))}
        </ol>
        <p className={cx(CAPTION, "mt-4 mb-0")}>
          And round again: what changed shows on the Changes page the next day, with who did it.
        </p>
      </SectionCard>

      <SectionCard icon="compass" title="Where to start" hint="It depends on what you do here.">
        <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4">
          {ROLES.map((r) => (
            <div key={r.who} className="rounded-xl border border-secondary p-4">
              <div className="mb-2 flex items-center gap-3">
                <FeaturedIcon icon={NAV_ICONS[r.icon]} color="gray" theme="light" size="md" />
                <h3 className="m-0 text-md font-semibold text-primary">{r.who}</h3>
              </div>
              <ul className="m-0 flex flex-col gap-1.5 pl-5 text-sm text-secondary">
                {r.points.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        icon="reviews"
        title="In a review: four decisions"
        hint="For each grant, and for the owners and secrets the review brings. The advice pre-fills one; you confirm or not."
      >
        <ul className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4 p-0">
          {DECISIONS.map((d) => (
            <li key={d.name} className="flex items-start gap-3">
              <FeaturedIcon icon={NAV_ICONS[d.icon]} color={d.color} theme="light" size="md" />
              <div>
                <h3 className="m-0 text-md font-semibold text-primary">{d.name}</h3>
                <p className="m-0 text-sm text-tertiary">{d.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>

      <SectionCard
        icon="look"
        title="On a finding: three ways to set it aside"
        hint="On Needs a look, any time. Each decision sticks from one check to the next; Reopen undoes it."
      >
        <div className={TABLE_SCROLL}>
          <table className={TABLE_COMPACT}>
            <thead>
              <tr>
                <th scope="col">Button</th>
                <th scope="col">What it says</th>
                <th scope="col">How long</th>
                <th scope="col">The advice</th>
              </tr>
            </thead>
            <tbody>
              {SET_ASIDE.map((s) => (
                <tr key={s.name}>
                  <th scope="row" className="font-semibold whitespace-nowrap">
                    {s.name}
                  </th>
                  <td>{s.meaning}</td>
                  <td className="whitespace-nowrap">{s.lasts}</td>
                  <td>{s.advice}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={cx(CAPTION, "mt-3 mb-0")}>
          Rule of thumb: accept a risk only when the answer is final, with a reason an auditor would accept. A plain
          Keep in a review only says &quot;fine for this month&quot;.
        </p>
      </SectionCard>

      <SectionCard icon="idea" title="Good to know">
        <ul className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4 p-0">
          {TIPS.map((t) => (
            <li key={t.text} className="flex items-start gap-3">
              <FeaturedIcon icon={NAV_ICONS[t.icon]} color="gray" theme="light" size="sm" />
              <p className="m-0 text-sm text-secondary">{t.text}</p>
            </li>
          ))}
        </ul>
      </SectionCard>
    </>
  );
}
