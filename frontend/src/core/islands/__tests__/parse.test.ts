/* Copyright 2026 Marimo. All rights reserved. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ISLAND_DATA_ATTRIBUTES } from "@/core/islands/constants";
import { parseMarimoIslandApps } from "../parse";
import { createMockIslands } from "./test-utils.tsx";

describe("parseMarimoIslandApps", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    document.body.removeChild(container);
  });

  it("should parse islands from document", () => {
    const elements = createMockIslands(2, "app1").map((el) => {
      el.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
      return el;
    });
    for (const el of elements) {
      container.appendChild(el);
    }

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result).toMatchInlineSnapshot(`
      [
        {
          "cells": [
            {
              "code": "cell_0 = 0",
              "idx": 0,
              "output": "<div>output 0</div>",
            },
            {
              "code": "cell_1 = 1",
              "idx": 1,
              "output": "<div>output 1</div>",
            },
          ],
          "id": "app1",
        },
      ]
    `);
  });

  it("should return empty array if no islands found", () => {
    const result = parseMarimoIslandApps(container);

    expect(result).toEqual([]);
  });

  it("should use custom root element", () => {
    const customRoot = document.createElement("div");
    const elements = createMockIslands(1, "app1").map((el) => {
      el.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
      return el;
    });
    for (const el of elements) {
      customRoot.appendChild(el);
    }

    const result = parseMarimoIslandApps(customRoot);

    expect(result).toHaveLength(1);
    expect(result).toMatchInlineSnapshot(`
      [
        {
          "cells": [
            {
              "code": "cell_0 = 0",
              "idx": 0,
              "output": "<div>output 0</div>",
            },
          ],
          "id": "app1",
        },
      ]
    `);
  });
});
