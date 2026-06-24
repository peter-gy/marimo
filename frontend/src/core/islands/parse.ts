/* Copyright 2026 Marimo. All rights reserved. */

import { Logger } from "@/utils/Logger";
import type { MarimoIslandApp } from "./app";
import { getIslandElements, parseIslandElementsIntoApps } from "./dom";

/**
 * Parses marimo island apps from the DOM
 * @param root - Root element to search within (defaults to document)
 */
export function parseMarimoIslandApps(
  root: Document | Element = document,
): MarimoIslandApp[] {
  const embeds = getIslandElements(root);
  if (embeds.length === 0) {
    Logger.warn("No embedded marimo apps found.");
    return [];
  }

  return parseIslandElementsIntoApps(embeds);
}
