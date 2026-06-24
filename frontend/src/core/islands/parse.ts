/* Copyright 2026 Marimo. All rights reserved. */

import { Logger } from "@/utils/Logger";
import { getIslandElements, parseIslandElementsIntoApps } from "./dom";
import {
  parseIslandElementsWithPayload,
  parseMarimoIslandPayloadResult,
} from "./payload";
import type { MarimoIslandApp } from "./app";

/**
 * Parses marimo island apps from the DOM
 * @param root - Root element to search within (defaults to document)
 */
export function parseMarimoIslandApps(
  root: Document | Element = document,
): MarimoIslandApp[] {
  const payloadResult = parseMarimoIslandPayloadResult(root);
  const domEmbeds = getIslandElements(root);
  if (domEmbeds.length === 0) {
    if (payloadResult !== null) {
      return payloadResult.apps;
    }
    Logger.warn("No embedded marimo apps found.");
    return [];
  }

  if (!payloadResult) {
    return parseIslandElementsIntoApps(domEmbeds);
  }

  return parseIslandElementsWithPayload(domEmbeds, payloadResult);
}
