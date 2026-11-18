import { describe, expect, it } from "vitest";

import type { DataKept } from "@/api/client";

import { changedValues, checksEstimate, daysText, isValid } from "./dataKeptUtils";

const data = {
  values: { snapshot_keep_days: 0, snapshot_keep_per_scope: 6, compact_after_days: 4, audit_retention_days: 400 },
  sources: { snapshot_keep_days: "server", snapshot_keep_per_scope: "server", compact_after_days: "server", audit_retention_days: "saved" },
  server: { snapshot_keep_days: 0, snapshot_keep_per_scope: 6, compact_after_days: 4, audit_retention_days: 365 },
  bounds: { snapshot_keep_days: [0, 3650], snapshot_keep_per_scope: [1, 1000], compact_after_days: [3, 3650], audit_retention_days: [0, 3650] },
  never: ["compact_after_days"],
  stored: {},
  folded_until: null,
  bytes_per_check: 6_500_000,
  checks_per_day: 1,
  retention_interval_hours: 24,
} as unknown as DataKept;

describe("Data kept", () => {
  it("sends only what changed, and clears a value set back to the server's", () => {
    expect(changedValues(data, { ...data.values })).toEqual({});
    expect(changedValues(data, { ...data.values, snapshot_keep_days: 365, audit_retention_days: 365 })).toEqual({
      snapshot_keep_days: 365,
      audit_retention_days: null,
    });
  });

  it("checks each value against its bounds, with 0 meaning never where allowed", () => {
    expect(isValid(data, "snapshot_keep_per_scope", 0)).toBe(false);
    expect(isValid(data, "compact_after_days", 0)).toBe(true);
    expect(isValid(data, "compact_after_days", 2)).toBe(false);
    expect(isValid(data, "snapshot_keep_days", 1.5)).toBe(false);
  });

  it("estimates what keeping every check costs, and says 0 means forever", () => {
    expect(checksEstimate(data, 0)).toBeNull();
    expect(checksEstimate(data, 365)).toEqual({ text: "About 365 checks, around 2.21 GB.", large: true });
    expect(checksEstimate(data, 30)?.large).toBe(false);
    expect(daysText(0)).toBe("kept forever");
    expect(daysText(1)).toBe("1 day");
  });
});
