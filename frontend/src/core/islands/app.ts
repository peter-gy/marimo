/* Copyright 2026 Marimo. All rights reserved. */

export interface MarimoIslandApp {
  /**
   * ID since we allow multiple apps on the same page.
   */
  id: string;
  /**
   * Cells in the app.
   */
  cells: MarimoIslandCell[];
}

export interface MarimoIslandCell {
  /**
   * Output of the cell.
   */
  output: string;
  /**
   * Code of the cell.
   */
  code: string;
  /**
   * Index of the cell.
   */
  idx: number;
}

export function createMarimoFile(app: {
  cells: Pick<MarimoIslandCell, "code">[];
}): string {
  const lines = [
    "import marimo",
    "app = marimo.App()",
    app.cells
      .map((cell) => {
        const code = cell.code
          .split("\n")
          .map((line) => `    ${line}`)
          .join("\n");

        // TODO: Handle async cells better.
        // This is probably not the best way to check if the code is async.
        // Ideally this is pushed into the Python code.
        const isAsync = code.includes("await ");
        const prefix = isAsync ? "async def" : "def";

        return `@app.cell\n${prefix} __():\n${code}\n    return`;
      })
      .join("\n"),
  ];

  return lines.join("\n");
}
