/* Copyright 2026 Marimo. All rights reserved. */

import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cellId } from "@/__tests__/branded";
import { initialNotebookState, notebookAtom } from "@/core/cells/cells";
import { ISLAND_DATA_ATTRIBUTES } from "@/core/islands/constants";
import type { CellId } from "@/core/cells/ids";
import { store } from "@/core/state/jotai";
import { MultiColumn } from "@/utils/id-tree";
import { MarimoIslandElement } from "../web-components";

describe("MarimoIslandElement", () => {
  beforeEach(() => {
    store.set(notebookAtom, {
      ...initialNotebookState(),
      cellIds: MultiColumn.from([
        [cellId("runtime-first"), cellId("runtime-second")],
      ]),
    });
  });

  it("should prefer the runtime cell index over the source cell id", () => {
    const element = createElement({
      [ISLAND_DATA_ATTRIBUTES.REACTIVE]: "true",
      [ISLAND_DATA_ATTRIBUTES.CELL_ID]: "source-cell",
      [ISLAND_DATA_ATTRIBUTES.CELL_IDX]: "1",
    });

    expect(element.cellId).toBe("runtime-second");
  });

  it("should fall back to the source cell id before parsing assigns an index", () => {
    const element = createElement({
      [ISLAND_DATA_ATTRIBUTES.REACTIVE]: "true",
      [ISLAND_DATA_ATTRIBUTES.CELL_ID]: "source-cell",
    });

    expect(element.cellId).toBe("source-cell");
  });

  it("should not resolve a cell id for non-reactive islands", () => {
    const element = createElement({
      [ISLAND_DATA_ATTRIBUTES.REACTIVE]: "false",
      [ISLAND_DATA_ATTRIBUTES.CELL_ID]: "source-cell",
      [ISLAND_DATA_ATTRIBUTES.CELL_IDX]: "1",
    });

    expect(element.cellId).toBeUndefined();
  });

  it("should preserve editors for non-reactive islands", () => {
    const renderRoot = vi.fn();
    const element = createElement({
      [ISLAND_DATA_ATTRIBUTES.REACTIVE]: "false",
    }) as unknown as StaticRenderElement;
    element.root = { render: renderRoot };

    element.renderIsland({
      html: "",
      codeCallback: () => "",
      editor: <div data-testid="static-editor" />,
      cellId: undefined,
    });

    render(renderRoot.mock.calls[0][0]);
    expect(screen.getByTestId("static-editor")).toBeTruthy();
  });
});

type StaticRenderElement = {
  root: { render: ReturnType<typeof vi.fn> };
  renderIsland: (config: {
    html: string;
    codeCallback: () => string;
    editor: ReactElement | null;
    cellId: CellId | undefined;
  }) => void;
};

function createElement(
  attributes: Record<string, string>,
): MarimoIslandElement {
  const element = Object.create(
    MarimoIslandElement.prototype,
  ) as MarimoIslandElement & {
    getAttribute: (name: string) => string | null;
  };
  element.getAttribute = (name: string) => attributes[name] ?? null;
  return element;
}
