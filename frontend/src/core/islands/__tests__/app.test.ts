/* Copyright 2026 Marimo. All rights reserved. */

import { describe, expect, it } from "vitest";
import { createMarimoFile } from "../app";

describe("createMarimoFile", () => {
  it("should return a string", () => {
    const app = {
      cells: [
        {
          code: 'print("Hello, World!")',
        },
      ],
    };
    const result = createMarimoFile(app);
    expect(typeof result).toBe("string");
  });

  it("should correctly format a single cell", () => {
    const app = {
      cells: [
        {
          code: 'print("Hello, World!")',
        },
      ],
    };
    const result = createMarimoFile(app);
    expect(result).toMatchInlineSnapshot(`
      "import marimo
      app = marimo.App()
      @app.cell
      def __():
          print("Hello, World!")
          return"
    `);
  });

  it("should correctly format multiple cells", () => {
    const app = {
      cells: [
        {
          code: 'print("Hello, World!")',
        },
        {
          code: 'print("Goodbye, World!")',
        },
      ],
    };
    const result = createMarimoFile(app);
    expect(result).toMatchInlineSnapshot(`
      "import marimo
      app = marimo.App()
      @app.cell
      def __():
          print("Hello, World!")
          return
      @app.cell
      def __():
          print("Goodbye, World!")
          return"
    `);
  });

  it("should create an async marimo file from cells", () => {
    const app = {
      cells: [{ code: "await asyncio.sleep(1)" }],
    };

    const result = createMarimoFile(app);

    expect(result).toMatchInlineSnapshot(`
      "import marimo
      app = marimo.App()
      @app.cell
      async def __():
          await asyncio.sleep(1)
          return"
    `);
  });

  it("should properly indent multi-line code", () => {
    const app = {
      cells: [{ code: "if True:\n    print('hello')\n    print('world')" }],
    };

    const result = createMarimoFile(app);

    expect(result).toMatchInlineSnapshot(`
      "import marimo
      app = marimo.App()
      @app.cell
      def __():
          if True:
              print('hello')
              print('world')
          return"
    `);
  });
});
