import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Coverage, Job, JobFilter, JobSchedule, JobScheduleUpdate, SubscriptionCoverage } from "@/api/client";
import { ToastProvider } from "@/components/Toast";

const SUB = "aaaaaaaa-0000-0000-0000-000000000001";
let schedules: JobSchedule[];
let jobs: Job[];
let covered: SubscriptionCoverage[];
const updateSchedule = vi.fn(async (id: number, body: JobScheduleUpdate) => {
  schedules = schedules.map((s) => (s.id === id ? { ...s, ...body } : s));
  return schedules.find((s) => s.id === id)!;
});
const deleteSchedule = vi.fn(async (_id: number) => undefined);
const listJobs = vi.fn(async (_limit: number, filter: JobFilter = {}) =>
  jobs.filter((j) => (!filter.kind || j.kind === filter.kind) && (!filter.status || j.status === filter.status)),
);

vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: {
    jobs: {
      schedules: async () => schedules,
      list: (limit: number, filter?: JobFilter) => listJobs(limit, filter),
      updateSchedule: (id: number, body: JobScheduleUpdate) => updateSchedule(id, body),
      coverage: async (): Promise<Coverage> => ({
        azure_keeps_days: 90, at_risk_days_left: 30, subscriptions: covered, second_pass_hours: 48,
        second_pass_finished_at: new Date().toISOString(), second_pass_subscriptions: covered.length,
        second_pass_late_events: 0, second_pass_failed: 0,
        entra_audit: { status: null, checked_at: null, message: null, schedule_id: null, enabled: false, collecting: false, covered_until: null },
      }),
      deleteSchedule: (id: number) => deleteSchedule(id),
    },
    freshness: async () => ({
      access_as_of: null, activity_up_to: null, daily_check_at_hour: 6, timezone: "Europe/Zurich",
      next_check_at: null, last_check: null,
      warnings: schedules.some((s) => !s.enabled)
        ? [{ kind: "schedules_off", text: "A schedule is off: the Activity Log of prod.", names: ["prod"], path: "/admin/data" }]
        : [],
    }),
    scopeBrowser: { subscriptions: async () => [{ id: SUB, display_name: "prod", scope: `/subscriptions/${SUB}` }] },
    scopes: { tree: async () => [] },
    database: { retentionPolicy: async () => { throw new Error("admins only"); } },
  },
}));

import JobsPage from "./JobsPage";

function schedule(over: Partial<JobSchedule>): JobSchedule {
  return {
    id: 1, kind: "activity_pull", target: SUB, every_hours: 1, at_hour: null, enabled: true,
    initial_window_days: 90, next_run_at: new Date(Date.now() + 600_000).toISOString(), last_run_at: null,
    last_job_id: null, last_job_status: null, last_error: null, failures: 0, created_at: "", ...over,
  };
}

function job(over: Partial<Job>): Job {
  return {
    kind: "activity_pull", id: 1, target: SUB, status: "done", progress_message: null, error: null,
    created_at: "2026-11-10T08:00:00Z", started_at: "2026-11-10T08:00:00Z", finished_at: "2026-11-10T08:00:08Z",
    window_start: null, window_end: null, events_persisted: 3, schedule_id: 1, origin: "schedule", ...over,
  };
}

function sub(over: Partial<SubscriptionCoverage>): SubscriptionCoverage {
  return {
    subscription_id: SUB, name: "prod", schedule_id: 1, enabled: true, collecting: false,
    covered_until: new Date(Date.now() - 18 * 60_000).toISOString(), behind_hours: 0.3, days_left: 88,
    state: "ok", second_pass: null, ...over,
  };
}

function show() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <JobsPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

/** Open a row's "More" menu and pick an entry. */
async function more(entry: string) {
  fireEvent.click(await screen.findByRole("button", { name: "More actions for prod" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: entry }));
}

beforeEach(() => {
  schedules = [schedule({})];
  jobs = [];
  covered = [sub({})];
  updateSchedule.mockClear();
  deleteSchedule.mockClear();
  listJobs.mockClear();
});
afterEach(cleanup);

describe("Data & schedules: pausing", () => {
  it("keeps Pause behind More, asks first, and the toast can undo it", async () => {
    show();
    await screen.findByRole("button", { name: "Run now" });
    // The only button next to "Run now" is "More": a stray click cannot pause.
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();

    await more("Pause");
    expect(updateSchedule).not.toHaveBeenCalled();
    const ask = await screen.findByRole("group", { name: "Confirm pause" });
    expect(ask.textContent).toContain("Pause collection for prod? Changes there stop being recorded");
    fireEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(updateSchedule).not.toHaveBeenCalled();

    await more("Pause");
    fireEvent.click(within(await screen.findByRole("group", { name: "Confirm pause" })).getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(updateSchedule).toHaveBeenCalledWith(1, { enabled: false }));

    // The page now says so: a warning with "Resume all", the row marked Paused with a Resume button.
    expect(await screen.findByText("A schedule is off: the Activity Log of prod.")).toBeTruthy();
    expect(await screen.findByText("Paused")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Resume" })).toBeTruthy();
    expect((await screen.findByText("Schedules", { selector: ".tile-label" })).closest(".status-tile")?.textContent).toContain(
      "0 on, 1 paused",
    );

    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(updateSchedule).toHaveBeenLastCalledWith(1, { enabled: true }));
  });

  it("resumes every paused schedule from the warning", async () => {
    schedules = [schedule({ id: 1, enabled: false }), schedule({ id: 2, target: "bbbbbbbb-0000-0000-0000-000000000002", enabled: false })];
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Resume all" }));
    await waitFor(() => expect(updateSchedule).toHaveBeenCalledTimes(2));
    expect(updateSchedule.mock.calls.map((c) => c[1])).toEqual([{ enabled: true }, { enabled: true }]);
  });

  it("says since when and by whom a schedule is paused", async () => {
    schedules = [schedule({ enabled: false, paused_at: new Date().toISOString(), paused_by: "anna@example.com" })];
    show();
    expect((await screen.findByText(/^since today .*, by anna@example\.com$/)).textContent).toBeTruthy();
  });
});

describe("Data & schedules: editing and deleting", () => {
  it("edits how often a schedule runs, and at which hour for a daily one", async () => {
    show();
    await more("Edit");
    const form = await screen.findByRole("form", { name: "Edit the schedule for prod" });
    fireEvent.change(within(form).getByLabelText("Repeats"), { target: { value: "24" } });
    fireEvent.change(within(form).getByLabelText("At (Europe/Zurich)"), { target: { value: "7" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(updateSchedule).toHaveBeenCalledWith(1, { every_hours: 24, at_hour: 7, initial_window_days: 90 }),
    );
  });

  it("asks before deleting", async () => {
    show();
    await more("Delete");
    expect(deleteSchedule).not.toHaveBeenCalled();
    fireEvent.click(within(await screen.findByRole("group", { name: "Confirm delete" })).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteSchedule).toHaveBeenCalledWith(1));
  });
});

describe("Data & schedules: at a glance", () => {
  const ROOT = "/providers/microsoft.management/managementgroups/root";
  const daily = (over: Partial<JobSchedule>) =>
    schedule({ id: 5, kind: "snapshot", target: ROOT, every_hours: 24, at_hour: 6, ...over });
  const tile = async (label: string) =>
    (await screen.findByText(label, { selector: ".tile-label" })).closest(".status-tile") as HTMLElement;

  it("is green when everything runs", async () => {
    schedules = [daily({}), schedule({})];
    show();
    await waitFor(async () => expect((await tile("Who has which role")).className).toContain("tone-ok"));
    expect((await tile("Who changed what")).textContent).toContain("Up to date");
    expect((await tile("Who changed what")).textContent).toContain("All 1 subscriptions collected");
    expect((await tile("Schedules")).textContent).toContain("2 on");
    expect(screen.getByText("Second pass today", { exact: false }).textContent).toContain("0 late events");
    // The daily check is the first row, on, and its hour is edited from its menu.
    const row = (await screen.findByText("Daily at 06:00")).closest("tr") as HTMLElement;
    expect(within(row).getByText("On")).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: "More actions for root" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const form = await screen.findByRole("form", { name: "Edit the schedule for root" });
    fireEvent.change(within(form).getByLabelText("At (Europe/Zurich)"), { target: { value: "5" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateSchedule).toHaveBeenCalledWith(5, { every_hours: 24, at_hour: 5 }));
  });

  it("turns amber when the daily check is paused, and red when Azure is about to forget", async () => {
    schedules = [daily({ enabled: false }), schedule({ enabled: false })];
    covered = [
      sub({ state: "at_risk", enabled: false, behind_hours: 70 * 24, days_left: 19 }),
      sub({ subscription_id: "management-groups", name: "Management groups" }),
    ];
    show();
    await waitFor(async () => expect((await tile("Who has which role")).className).toContain("tone-warn"));
    expect((await tile("Who has which role")).textContent).toContain("Paused");
    const collection = await tile("Who changed what");
    expect(collection.className).toContain("tone-danger");
    expect(collection.textContent).toContain("1 of 2 behind");
    expect(collection.textContent).toContain("prod: 70 days behind, 19 days left");
    expect((await tile("Schedules")).textContent).toContain("0 on, 2 paused");
    // The coverage row says the same, with the days left.
    const list = await screen.findByRole("list", { name: /^Activity Log coverage by subscription/ });
    expect(within(list).getByText("70 days behind · paused")).toBeTruthy();
    expect(within(list).getByText("19 days left")).toBeTruthy();
    // The management groups' logs are one more row, collected like a subscription's.
    expect(within(list).getByText("Management groups")).toBeTruthy();
    expect(within(list).queryByText("Not covered")).toBeNull();
  });
});

describe("Data & schedules: recent runs", () => {
  it("filters on the server and explains failures in words", async () => {
    jobs = [
      job({ id: 7, status: "failed", error: "ClientAuthenticationError: [SSL: UNEXPECTED_EOF]", schedule_id: null, origin: "manual",
            finished_at: "2026-11-10T08:01:39Z",
            error_summary: "Could not reach Azure: a network, proxy or certificate problem on the way out." }),
      job({ id: 8 }),
    ];
    show();
    expect(await screen.findByText("8 s")).toBeTruthy(); // how long run #8 took
    expect(screen.getByText("1 min 39 s")).toBeTruthy();
    expect(screen.getByText("started by hand")).toBeTruthy();
    expect(screen.getByText("scheduled")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Failed" }));
    await waitFor(() => expect(listJobs).toHaveBeenLastCalledWith(50, { status: "failed" }));
    await waitFor(() => expect(screen.queryByText("8 s")).toBeNull());
    expect(await screen.findByText(/^Could not reach Azure/)).toBeTruthy();
    expect(screen.getByText("ClientAuthenticationError: [SSL: UNEXPECTED_EOF]")).toBeTruthy(); // behind Details
  });
});
