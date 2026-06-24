/* Copyright 2026 Marimo. All rights reserved. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { UIElementId } from "@/core/cells/ids";
import { OBJECT_ID_ATTR } from "@/core/dom/ui-element-constants";
import { UI_ELEMENT_REGISTRY } from "@/core/dom/uiregistry";
import {
  ISLAND_DATA_ATTRIBUTES,
  ISLANDS_JSON_SCRIPT_TYPE,
  ISLAND_TAG_NAMES,
} from "@/core/islands/constants";
import { extractIslandCodeFromEmbed } from "../dom";
import { parseMarimoIslandApps } from "../parse";
import { createMockIslandElement, createMockIslands } from "./test-utils.tsx";

describe("parseMarimoIslandApps", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    document.body.removeChild(container);
    UI_ELEMENT_REGISTRY.entries.clear();
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

  it("should prefer supported JSON payloads over DOM island code", () => {
    const element = createMockIslandElement({
      appId: "app-1",
      code: "dom_code = True",
      innerHTML: "<div>DOM output</div>",
    });
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    container.appendChild(element);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "app-1",
      cells: [
        {
          cellId: "cell-1",
          code: "payload_code = True",
          outputHtml: "<div>Payload output</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toEqual([
      {
        id: "app-1",
        cells: [
          {
            cellId: "cell-1",
            code: "payload_code = True",
            idx: 0,
            output: "<div>Payload output</div>",
          },
        ],
      },
    ]);
    expect(extractIslandCodeFromEmbed(element)).toBe("payload_code = True");
  });

  it("should update registered editor values from payload code", () => {
    const objectId = "editor-1" as UIElementId;
    const element = document.createElement(ISLAND_TAG_NAMES.ISLAND);
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.APP_ID, "app-1");
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    const output = document.createElement(ISLAND_TAG_NAMES.CELL_OUTPUT);
    output.innerHTML = "<div>DOM output</div>";
    element.appendChild(output);
    const editorWrapper = document.createElement("div");
    editorWrapper.setAttribute(OBJECT_ID_ATTR, objectId);
    const editor = document.createElement(ISLAND_TAG_NAMES.CODE_EDITOR);
    editor.setAttribute(
      "data-initial-value",
      JSON.stringify("dom_code = True"),
    );
    editorWrapper.appendChild(editor);
    element.appendChild(editorWrapper);
    UI_ELEMENT_REGISTRY.set(objectId, "dom_code = True");
    container.appendChild(element);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "app-1",
      cells: [
        {
          cellId: "cell-1",
          code: "payload_code = True",
          outputHtml: "<div>Payload output</div>",
          reactive: true,
          displayCode: true,
          displayOutput: true,
        },
      ],
    });

    parseMarimoIslandApps(container);

    expect(UI_ELEMENT_REGISTRY.lookupValue(objectId)).toBe(
      "payload_code = True",
    );
    expect(editor.getAttribute("data-initial-value")).toBe(
      JSON.stringify("payload_code = True"),
    );
  });

  it("should fall back to DOM islands for unsupported payload versions", () => {
    const element = createMockIslandElement({
      appId: "app-1",
      code: "dom_code = True",
      innerHTML: "<div>DOM output</div>",
    });
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    container.appendChild(element);
    appendPayload(container, {
      schemaVersion: 2,
      appId: "app-1",
      cells: [],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toEqual([
      {
        id: "app-1",
        cells: [
          {
            code: "dom_code = True",
            idx: 0,
            output: "<div>DOM output</div>",
          },
        ],
      },
    ]);
  });

  it("should use payload order for runtime cell indices", () => {
    const second = createMockIslandElement({
      appId: "app-1",
      code: "dom_second = True",
      innerHTML: "<div>second</div>",
    });
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-b");
    const first = createMockIslandElement({
      appId: "app-1",
      code: "dom_first = True",
      innerHTML: "<div>first</div>",
    });
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-a");
    container.append(second, first);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "app-1",
      cells: [
        {
          cellId: "cell-a",
          code: "payload_first = True",
          outputHtml: "<div>first</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
        {
          cellId: "cell-b",
          code: "payload_second = True",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "payload_first = True",
      "payload_second = True",
    ]);
    expect(first.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("0");
    expect(second.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("1");
  });

  it("should merge payload scripts with the same app id", () => {
    const first = createMockIslandElement({
      appId: "main",
      code: "dom_first = True",
      innerHTML: "<div>first</div>",
    });
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-a");
    const second = createMockIslandElement({
      appId: "main",
      code: "dom_second = True",
      innerHTML: "<div>second</div>",
    });
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-b");
    container.append(first, second);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-a",
          code: "payload_first = True",
          outputHtml: "<div>first</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-b",
          code: "payload_second = True",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("main");
    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "payload_first = True",
      "payload_second = True",
    ]);
    expect(first.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("0");
    expect(second.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("1");
  });

  it("should keep duplicate payload cell ids as distinct occurrences", () => {
    const first = createMockIslandElement({
      appId: "main",
      code: "dom_first = True",
      innerHTML: "<div>first</div>",
    });
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    const second = createMockIslandElement({
      appId: "main",
      code: "dom_second = True",
      innerHTML: "<div>second</div>",
    });
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    container.append(first, second);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-1",
          code: "payload_first = True",
          outputHtml: "<div>first</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-1",
          code: "payload_second = True",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "payload_first = True",
      "payload_second = True",
    ]);
    expect(first.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("0");
    expect(second.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("1");
    expect(extractIslandCodeFromEmbed(first)).toBe("payload_first = True");
    expect(extractIslandCodeFromEmbed(second)).toBe("payload_second = True");
  });

  it("should count duplicate payload cell ids by reactivity", () => {
    const first = createMockIslandElement({
      appId: "main",
      code: "dom_first = True",
      innerHTML: "<div>first</div>",
    });
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    first.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    const staticIsland = createMockIslandElement({
      appId: "main",
      code: "",
      innerHTML: "<span></span>",
    });
    staticIsland.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "false");
    staticIsland.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    staticIsland.removeAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX);
    const second = createMockIslandElement({
      appId: "main",
      code: "dom_second = True",
      innerHTML: "<div>second</div>",
    });
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    second.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    container.append(first, staticIsland, second);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-1",
          code: "payload_first = True",
          outputHtml: "<div>first</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
        {
          cellId: "cell-1",
          code: "",
          outputHtml: "<span></span>",
          reactive: false,
          displayCode: false,
          displayOutput: true,
        },
        {
          cellId: "cell-1",
          code: "payload_second = True",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "payload_first = True",
      "payload_second = True",
    ]);
    expect(first.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("0");
    expect(
      staticIsland.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX),
    ).toBeNull();
    expect(second.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("1");
  });

  it("should not attach reactive payload cells to non-reactive duplicate ids", () => {
    const staticIsland = createMockIslandElement({
      appId: "main",
      code: "",
      innerHTML: "<span></span>",
    });
    staticIsland.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "false");
    staticIsland.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-b");
    staticIsland.removeAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX);
    const reactiveIsland = createMockIslandElement({
      appId: "main",
      code: "dom_second = True",
      innerHTML: "<div>second</div>",
    });
    reactiveIsland.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    reactiveIsland.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-b");
    container.append(staticIsland, reactiveIsland);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-b",
          code: "payload_second = True",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "payload_second = True",
    ]);
    expect(
      staticIsland.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX),
    ).toBeNull();
    expect(reactiveIsland.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe(
      "0",
    );
    expect(extractIslandCodeFromEmbed(reactiveIsland)).toBe(
      "payload_second = True",
    );
  });

  it("should not replace DOM-only duplicate islands with later payload cells", () => {
    const domOnly = createMockIslandElement({
      appId: "main",
      code: "dom_first = True",
      innerHTML: "<div>first</div>",
    });
    domOnly.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    domOnly.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    const payloadBacked = createMockIslandElement({
      appId: "main",
      code: "payload_second = True",
      innerHTML: "<div>second</div>",
    });
    payloadBacked.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    payloadBacked.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    container.append(domOnly, payloadBacked);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-1",
          code: "payload_second = True",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "dom_first = True",
      "payload_second = True",
    ]);
    expect(domOnly.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("0");
    expect(payloadBacked.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe(
      "1",
    );
  });

  it("should preserve DOM order when another cell has a payload", () => {
    const domOnly = createMockIslandElement({
      appId: "main",
      code: "dom_first = True",
      innerHTML: "<div>first</div>",
    });
    domOnly.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    domOnly.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-a");
    const payloadBacked = createMockIslandElement({
      appId: "main",
      code: "dom_second = True",
      innerHTML: "<div>second</div>",
    });
    payloadBacked.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    payloadBacked.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-b");
    container.append(domOnly, payloadBacked);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-b",
          code: "payload_second = True",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("main");
    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "dom_first = True",
      "payload_second = True",
    ]);
    expect(domOnly.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("0");
    expect(payloadBacked.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe(
      "1",
    );
    expect(extractIslandCodeFromEmbed(payloadBacked)).toBe(
      "payload_second = True",
    );
  });

  it("should keep payload-only cells before the next payload-backed island", () => {
    const domOnly = createMockIslandElement({
      appId: "main",
      code: "dom_first = setup",
      innerHTML: "<div>first</div>",
    });
    domOnly.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    domOnly.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-a");
    const payloadBacked = createMockIslandElement({
      appId: "main",
      code: "dom_second = setup",
      innerHTML: "<div>second</div>",
    });
    payloadBacked.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "true");
    payloadBacked.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-b");
    container.append(domOnly, payloadBacked);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "main",
      cells: [
        {
          cellId: "cell-setup",
          code: "setup = True",
          outputHtml: "",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
        {
          cellId: "cell-b",
          code: "payload_second = setup",
          outputHtml: "<div>second</div>",
          reactive: true,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toHaveLength(1);
    expect(result[0].cells.map((cell) => cell.code)).toEqual([
      "setup = True",
      "dom_first = setup",
      "payload_second = setup",
    ]);
    expect(domOnly.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe("1");
    expect(payloadBacked.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBe(
      "2",
    );
  });

  it("should not start apps for non-reactive payload cells", () => {
    const element = createMockIslandElement({
      appId: "app-1",
      code: "dom_code = True",
      innerHTML: "<div>DOM output</div>",
    });
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE, "false");
    element.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID, "cell-1");
    element.removeAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX);
    container.appendChild(element);
    appendPayload(container, {
      schemaVersion: 1,
      appId: "app-1",
      cells: [
        {
          cellId: "cell-1",
          code: "payload_code = True",
          outputHtml: "<div>Payload output</div>",
          reactive: false,
          displayCode: false,
          displayOutput: true,
        },
      ],
    });

    const result = parseMarimoIslandApps(container);

    expect(result).toEqual([]);
    expect(element.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX)).toBeNull();
  });

  it("should return empty array if no islands found", () => {
    const result = parseMarimoIslandApps(container);

    expect(result).toEqual([]);
  });

  it("should accept custom root element", () => {
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

function appendPayload(container: HTMLElement, payload: unknown): void {
  const script = document.createElement("script");
  script.type = ISLANDS_JSON_SCRIPT_TYPE;
  script.textContent = JSON.stringify(payload);
  container.appendChild(script);
}
