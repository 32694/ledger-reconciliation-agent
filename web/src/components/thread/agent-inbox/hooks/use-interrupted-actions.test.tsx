// @vitest-environment jsdom

import { Interrupt } from "@langchain/langgraph-sdk";
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HITLRequest } from "../types";
import useInterruptedActions from "./use-interrupted-actions";

const mocks = vi.hoisted(() => ({
  submit: vi.fn(),
  toast: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/providers/Stream", () => ({
  useStreamContext: () => ({ submit: mocks.submit }),
}));

vi.mock("sonner", () => ({
  toast: Object.assign(mocks.toast, { error: mocks.toastError }),
}));

describe("useInterruptedActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("waits for a rejected resolution submission and reports failure", async () => {
    const pending = Promise.withResolvers<void>();
    mocks.submit.mockReturnValueOnce(pending.promise);
    const interrupt: Interrupt<HITLRequest> = { id: "interrupt-1" };
    const { result } = renderHook(() => useInterruptedActions({ interrupt }));
    let submission: Promise<void>;

    act(() => {
      submission = result.current.handleResolutionDecision("approve");
    });

    expect(result.current.loading).toBe(true);
    expect(result.current.streaming).toBe(true);

    await act(async () => {
      pending.reject(new Error("network error"));
      await submission;
    });

    expect(mocks.toast).not.toHaveBeenCalledWith("已提交", expect.anything());
    expect(mocks.toastError).toHaveBeenCalledWith(
      "提交失败",
      expect.objectContaining({
        description: "本次确认未提交，请稍后重试。",
      }),
    );
    expect(result.current.loading).toBe(false);
    expect(result.current.streaming).toBe(false);
    expect(result.current.streamFinished).toBe(false);
  });
});
