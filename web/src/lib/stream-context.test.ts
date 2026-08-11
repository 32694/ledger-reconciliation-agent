import { describe, expect, it } from "vitest";

import { extendWithoutReadingGetters } from "./stream-context";

describe("extendWithoutReadingGetters", () => {
  it("does not eagerly read SDK stream getters", () => {
    let getterReads = 0;
    const stream = Object.defineProperty(
      { messages: [] as unknown[] },
      "messages",
      {
        enumerable: true,
        get() {
          getterReads += 1;
          return [];
        },
      },
    );

    const context = extendWithoutReadingGetters(stream, {
      connectionStatus: "connected",
    });

    expect(getterReads).toBe(0);
    expect(context.connectionStatus).toBe("connected");
    expect(context.messages).toEqual([]);
    expect(getterReads).toBe(1);
  });
});
