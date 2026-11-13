import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { useActivePolling } from "./useActivePolling";

describe("useActivePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  test("does not call tick when disabled", () => {
    const tick = vi.fn();
    renderHook(() => useActivePolling(tick, 100, false));
    vi.advanceTimersByTime(500);
    expect(tick).not.toHaveBeenCalled();
  });

  test("polls at the requested interval when enabled", () => {
    const tick = vi.fn();
    renderHook(() => useActivePolling(tick, 100, true));
    vi.advanceTimersByTime(350);
    expect(tick).toHaveBeenCalledTimes(3);
  });

  test("stops polling when disabled mid-flight", () => {
    const tick = vi.fn();
    const { rerender } = renderHook(
      ({ enabled }) => useActivePolling(tick, 100, enabled),
      { initialProps: { enabled: true } },
    );
    vi.advanceTimersByTime(250);
    expect(tick).toHaveBeenCalledTimes(2);
    rerender({ enabled: false });
    vi.advanceTimersByTime(500);
    expect(tick).toHaveBeenCalledTimes(2);
  });

  test("pauses on visibilitychange to hidden and resumes on visible", () => {
    const tick = vi.fn();
    renderHook(() => useActivePolling(tick, 100, true));
    vi.advanceTimersByTime(150);
    expect(tick).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(500);
    // No additional ticks while hidden.
    expect(tick).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    // Resuming should fire an immediate tick AND restart the interval.
    expect(tick).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(100);
    expect(tick).toHaveBeenCalledTimes(3);
  });

  test("uses latest tick closure without re-subscribing", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(
      ({ fn }) => useActivePolling(fn, 100, true),
      { initialProps: { fn: first } },
    );
    vi.advanceTimersByTime(150);
    expect(first).toHaveBeenCalledTimes(1);

    rerender({ fn: second });
    vi.advanceTimersByTime(100);
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
  });
});
