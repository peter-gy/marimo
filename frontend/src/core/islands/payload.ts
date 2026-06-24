/* Copyright 2026 Marimo. All rights reserved. */

import type { UIElementId } from "@/core/cells/ids";
import { OBJECT_ID_ATTR } from "@/core/dom/ui-element-constants";
import { UI_ELEMENT_REGISTRY } from "@/core/dom/uiregistry";
import {
  ISLAND_DATA_ATTRIBUTES,
  ISLANDS_JSON_SCRIPT_TYPE,
  ISLAND_TAG_NAMES,
} from "@/core/islands/constants";
import { Logger } from "@/utils/Logger";
import type { MarimoIslandApp } from "./app";
import {
  extractIslandCodeFromEmbed,
  isReactiveIsland,
  parseIslandElement,
} from "./dom";

export interface MarimoIslandPayloadCell {
  cellId: string;
  code: string;
  outputHtml: string;
  reactive: boolean;
  displayCode: boolean;
  displayOutput: boolean;
}

export interface MarimoIslandPayload {
  schemaVersion: 1;
  appId: string;
  cells: MarimoIslandPayloadCell[];
}

export interface MarimoIslandPayloadParseResult {
  apps: MarimoIslandApp[];
  cellsByKey: Map<string, MarimoIslandPayloadCell[]>;
  cellsByApp: Map<string, MarimoIslandPayloadCell[]>;
}

interface MarimoIslandPayloadCellMatch {
  key: string;
  matchKey: string;
  occurrence: number;
  occurrenceKey: string;
  cell: MarimoIslandPayloadCell;
}

interface MarimoIslandPayloadCellEntry extends MarimoIslandPayloadCellMatch {
  occurrenceKey: string;
}

type MarimoIslandPayloadMergeItem =
  | {
      type: "payload";
      entry: MarimoIslandPayloadCellEntry;
      embed: HTMLElement;
    }
  | {
      type: "dom";
      embed: HTMLElement;
    };

export function parseMarimoIslandPayloadResult(
  root: Document | Element = document,
): MarimoIslandPayloadParseResult | null {
  const scripts = root.querySelectorAll<HTMLScriptElement>(
    `script[type="${ISLANDS_JSON_SCRIPT_TYPE}"]`,
  );
  if (scripts.length === 0) {
    return null;
  }

  const apps = new Map<string, MarimoIslandApp>();
  const cellsByKey = new Map<string, MarimoIslandPayloadCell[]>();
  const cellsByApp = new Map<string, MarimoIslandPayloadCell[]>();
  let sawSupportedPayload = false;
  for (const script of scripts) {
    const payload = parseMarimoIslandPayload(script.textContent);
    if (!payload) {
      continue;
    }

    sawSupportedPayload = true;
    for (const cell of payload.cells) {
      const key = islandCellKey(payload.appId, cell.cellId);
      if (!cellsByKey.has(key)) {
        cellsByKey.set(key, []);
      }
      cellsByKey.get(key)!.push(cell);
      if (!cellsByApp.has(payload.appId)) {
        cellsByApp.set(payload.appId, []);
      }
      cellsByApp.get(payload.appId)!.push(cell);
      if (!cell.reactive) {
        continue;
      }
      if (!apps.has(payload.appId)) {
        apps.set(payload.appId, { id: payload.appId, cells: [] });
      }
      const app = apps.get(payload.appId)!;
      app.cells.push({
        cellId: cell.cellId,
        code: cell.code,
        idx: app.cells.length,
        output: cell.outputHtml,
      });
    }
  }

  return sawSupportedPayload
    ? {
        apps: [...apps.values()].filter((app) => app.cells.length > 0),
        cellsByKey,
        cellsByApp,
      }
    : null;
}

function parseMarimoIslandPayload(
  text: string | null,
): MarimoIslandPayload | null {
  if (!text) {
    return null;
  }

  try {
    const value = JSON.parse(text) as unknown;
    return isMarimoIslandPayload(value) ? value : null;
  } catch {
    return null;
  }
}

function isMarimoIslandPayload(value: unknown): value is MarimoIslandPayload {
  if (!isRecord(value)) {
    return false;
  }
  if (value.schemaVersion !== 1) {
    return false;
  }
  if (typeof value.appId !== "string") {
    return false;
  }
  if (!Array.isArray(value.cells)) {
    return false;
  }
  return value.cells.every(isMarimoIslandPayloadCell);
}

function isMarimoIslandPayloadCell(
  value: unknown,
): value is MarimoIslandPayloadCell {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.cellId === "string" &&
    typeof value.code === "string" &&
    typeof value.outputHtml === "string" &&
    typeof value.reactive === "boolean" &&
    typeof value.displayCode === "boolean" &&
    typeof value.displayOutput === "boolean"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function parseIslandElementsWithPayload(
  embeds: HTMLElement[],
  payloadResult: MarimoIslandPayloadParseResult,
): MarimoIslandApp[] {
  const ambiguousMatchKeys = getAmbiguousPayloadMatchKeys(
    embeds,
    payloadResult,
  );
  const partialApps = getPartialPayloadApps(
    embeds,
    payloadResult,
    ambiguousMatchKeys,
  );
  const apps: MarimoIslandApp[] = [];

  for (const appId of getIslandAppIds(embeds, payloadResult)) {
    const app: MarimoIslandApp = { id: appId, cells: [] };
    const payloadCells = payloadResult.cellsByApp.get(appId) ?? [];
    const payloadEntries = getPayloadCellEntries(appId, payloadCells);
    const usePayloadOrder = payloadCells.length > 0 && !partialApps.has(appId);

    if (usePayloadOrder) {
      for (const entry of payloadEntries) {
        const island = findIslandForPayloadCell(
          embeds,
          appId,
          entry.cell.cellId,
          entry.occurrence,
          entry.cell.reactive,
        );
        appendPayloadCell(app, entry.cell, island);
      }
    } else {
      appendPartialPayloadApp(
        app,
        appId,
        embeds,
        payloadResult,
        payloadEntries,
        ambiguousMatchKeys,
      );
    }

    if (app.cells.length > 0) {
      apps.push(app);
    }
  }

  return apps;
}

function getAmbiguousPayloadMatchKeys(
  embeds: HTMLElement[],
  payloadResult: MarimoIslandPayloadParseResult,
): Set<string> {
  const domCounts = new Map<string, number>();
  for (const embed of embeds) {
    const appId = embed.getAttribute(ISLAND_DATA_ATTRIBUTES.APP_ID);
    const cellId = embed.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID);
    if (!appId || !cellId) {
      continue;
    }
    const matchKey = payloadMatchKey(appId, cellId, isReactiveIsland(embed));
    domCounts.set(matchKey, (domCounts.get(matchKey) ?? 0) + 1);
  }

  const payloadCounts = new Map<string, number>();
  for (const [appId, cells] of payloadResult.cellsByApp) {
    for (const cell of cells) {
      const matchKey = payloadMatchKey(appId, cell.cellId, cell.reactive);
      payloadCounts.set(matchKey, (payloadCounts.get(matchKey) ?? 0) + 1);
    }
  }

  const ambiguousMatchKeys = new Set<string>();
  for (const [matchKey, domCount] of domCounts) {
    if (domCount > (payloadCounts.get(matchKey) ?? 0)) {
      ambiguousMatchKeys.add(matchKey);
    }
  }
  return ambiguousMatchKeys;
}

function appendPartialPayloadApp(
  app: MarimoIslandApp,
  appId: string,
  embeds: HTMLElement[],
  payloadResult: MarimoIslandPayloadParseResult,
  payloadEntries: MarimoIslandPayloadCellEntry[],
  ambiguousMatchKeys: Set<string>,
): void {
  const entryByOccurrenceKey = new Map(
    payloadEntries.map((entry) => [entry.occurrenceKey, entry]),
  );
  const entryIndexByOccurrenceKey = new Map(
    payloadEntries.map((entry, index) => [entry.occurrenceKey, index]),
  );
  const consumedOccurrences = new Map<string, number>();
  const mergeItems: MarimoIslandPayloadMergeItem[] = [];

  for (const embed of embeds) {
    if (embed.getAttribute(ISLAND_DATA_ATTRIBUTES.APP_ID) !== appId) {
      continue;
    }

    const payloadCell = getPayloadCellForIsland(
      embed,
      payloadResult,
      consumedOccurrences,
      ambiguousMatchKeys,
    );
    if (payloadCell) {
      const entry = entryByOccurrenceKey.get(payloadCell.occurrenceKey);
      if (entry) {
        mergeItems.push({ type: "payload", entry, embed });
        consumedOccurrences.set(
          payloadCell.matchKey,
          payloadCell.occurrence + 1,
        );
        continue;
      }
    }

    mergeItems.push({ type: "dom", embed });
  }

  const appendedPayloadCells = new Set<string>();
  let payloadCursor = 0;
  const appendPayloadEntriesBefore = (occurrenceKey: string) => {
    const targetIndex =
      entryIndexByOccurrenceKey.get(occurrenceKey) ?? payloadEntries.length;
    while (payloadCursor < targetIndex) {
      const entry = payloadEntries[payloadCursor];
      if (!appendedPayloadCells.has(entry.occurrenceKey)) {
        appendPayloadCell(app, entry.cell);
        appendedPayloadCells.add(entry.occurrenceKey);
      }
      payloadCursor += 1;
    }
  };

  for (let index = 0; index < mergeItems.length; index++) {
    const item = mergeItems[index];
    if (item.type === "payload") {
      appendPayloadEntriesBefore(item.entry.occurrenceKey);
      appendPayloadCell(app, item.entry.cell, item.embed);
      appendedPayloadCells.add(item.entry.occurrenceKey);
      payloadCursor = Math.max(
        payloadCursor,
        (entryIndexByOccurrenceKey.get(item.entry.occurrenceKey) ?? -1) + 1,
      );
      continue;
    }

    const nextPayloadItem = mergeItems
      .slice(index + 1)
      .find((candidate) => candidate.type === "payload");
    if (nextPayloadItem?.type === "payload") {
      appendPayloadEntriesBefore(nextPayloadItem.entry.occurrenceKey);
    }
    appendDomCell(app, item.embed);
  }

  while (payloadCursor < payloadEntries.length) {
    const entry = payloadEntries[payloadCursor];
    if (!appendedPayloadCells.has(entry.occurrenceKey)) {
      appendPayloadCell(app, entry.cell);
    }
    payloadCursor += 1;
  }
}

function getPartialPayloadApps(
  embeds: HTMLElement[],
  payloadResult: MarimoIslandPayloadParseResult,
  ambiguousMatchKeys: Set<string>,
): Set<string> {
  const coverage = new Map<
    string,
    { reactiveCells: number; payloadBackedCells: number }
  >();
  const consumedOccurrences = new Map<string, number>();

  for (const embed of embeds) {
    const appId = embed.getAttribute(ISLAND_DATA_ATTRIBUTES.APP_ID);
    if (!appId) {
      Logger.warn("Embedded marimo cell missing data-app-id attribute.");
      continue;
    }
    if (embed.getAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE) !== "true") {
      continue;
    }

    const appCoverage = coverage.get(appId) ?? {
      reactiveCells: 0,
      payloadBackedCells: 0,
    };
    appCoverage.reactiveCells += 1;
    const payloadCell = getPayloadCellForIsland(
      embed,
      payloadResult,
      consumedOccurrences,
      ambiguousMatchKeys,
    );
    if (payloadCell) {
      appCoverage.payloadBackedCells += 1;
      consumedOccurrences.set(payloadCell.matchKey, payloadCell.occurrence + 1);
    }
    coverage.set(appId, appCoverage);
  }

  const partialApps = new Set<string>();
  for (const [appId, appCoverage] of coverage) {
    if (
      payloadResult.cellsByApp.has(appId) &&
      appCoverage.reactiveCells > appCoverage.payloadBackedCells
    ) {
      partialApps.add(appId);
    }
  }
  return partialApps;
}

function getIslandAppIds(
  embeds: HTMLElement[],
  payloadResult: MarimoIslandPayloadParseResult,
): string[] {
  const appIds = new Set<string>();
  for (const embed of embeds) {
    const appId = embed.getAttribute(ISLAND_DATA_ATTRIBUTES.APP_ID);
    if (appId) {
      appIds.add(appId);
    }
  }
  for (const appId of payloadResult.cellsByApp.keys()) {
    appIds.add(appId);
  }
  return [...appIds];
}

function getPayloadCellEntries(
  appId: string,
  cells: MarimoIslandPayloadCell[],
): MarimoIslandPayloadCellEntry[] {
  const occurrenceByMatchKey = new Map<string, number>();
  return cells.map((cell) => {
    const key = islandCellKey(appId, cell.cellId);
    const matchKey = payloadMatchKey(appId, cell.cellId, cell.reactive);
    const occurrence = occurrenceByMatchKey.get(matchKey) ?? 0;
    occurrenceByMatchKey.set(matchKey, occurrence + 1);
    return {
      key,
      matchKey,
      occurrence,
      occurrenceKey: payloadOccurrenceKey(matchKey, occurrence),
      cell,
    };
  });
}

function appendPayloadCell(
  app: MarimoIslandApp,
  cell: MarimoIslandPayloadCell,
  embed?: HTMLElement,
): void {
  materializeIslandPayloadCode(embed, cell);
  if (!cell.reactive) {
    return;
  }

  const idx = app.cells.length;
  app.cells.push({
    cellId: cell.cellId,
    code: cell.code,
    idx,
    output: cell.outputHtml,
  });
  embed?.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX, idx.toString());
}

function appendDomCell(app: MarimoIslandApp, embed: HTMLElement): void {
  const reactive =
    embed.getAttribute(ISLAND_DATA_ATTRIBUTES.REACTIVE) === "true";
  if (!reactive) {
    return;
  }

  const cellData = parseIslandElement(embed);
  if (!cellData) {
    Logger.warn(`Embedded marimo app ${app.id} missing cell output or code.`);
    return;
  }

  const idx = app.cells.length;
  app.cells.push({
    output: cellData.output,
    code: cellData.code,
    idx,
  });
  embed.setAttribute(ISLAND_DATA_ATTRIBUTES.CELL_IDX, idx.toString());
}

function getPayloadCellForIsland(
  embed: HTMLElement,
  payloadResult: MarimoIslandPayloadParseResult,
  consumedOccurrences: Map<string, number>,
  ambiguousMatchKeys: Set<string>,
): MarimoIslandPayloadCellMatch | undefined {
  const appId = embed.getAttribute(ISLAND_DATA_ATTRIBUTES.APP_ID);
  const cellId = embed.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID);
  if (!appId || !cellId) {
    return undefined;
  }
  const key = islandCellKey(appId, cellId);
  const embedReactive = isReactiveIsland(embed);
  const matchKey = payloadMatchKey(appId, cellId, embedReactive);
  const cells = payloadResult.cellsByKey.get(key);
  if (!cells) {
    return undefined;
  }
  const occurrence = consumedOccurrences.get(matchKey) ?? 0;
  let seenMatchingCells = 0;
  for (const cell of cells) {
    if (cell.reactive !== embedReactive) {
      continue;
    }
    if (seenMatchingCells < occurrence) {
      seenMatchingCells += 1;
      continue;
    }
    if (
      ambiguousMatchKeys.has(matchKey) &&
      extractIslandCodeFromEmbed(embed) !== cell.code
    ) {
      seenMatchingCells += 1;
      continue;
    }
    return {
      key,
      matchKey,
      occurrence: seenMatchingCells,
      occurrenceKey: payloadOccurrenceKey(matchKey, seenMatchingCells),
      cell,
    };
  }
  return undefined;
}

function findIslandForPayloadCell(
  embeds: HTMLElement[],
  appId: string,
  cellId: string,
  occurrence: number,
  reactive: boolean,
): HTMLElement | undefined {
  let seen = 0;
  for (const embed of embeds) {
    if (
      embed.getAttribute(ISLAND_DATA_ATTRIBUTES.APP_ID) !== appId ||
      embed.getAttribute(ISLAND_DATA_ATTRIBUTES.CELL_ID) !== cellId ||
      isReactiveIsland(embed) !== reactive
    ) {
      continue;
    }
    if (seen === occurrence) {
      return embed;
    }
    seen += 1;
  }
  return undefined;
}

function materializeIslandPayloadCode(
  embed: HTMLElement | undefined,
  cell: MarimoIslandPayloadCell,
): void {
  if (!embed) {
    return;
  }

  let codeElement = embed.querySelector<HTMLElement>(
    ISLAND_TAG_NAMES.CELL_CODE,
  );
  if (!codeElement) {
    codeElement = document.createElement(ISLAND_TAG_NAMES.CELL_CODE);
    codeElement.hidden = true;
    embed.appendChild(codeElement);
  }
  codeElement.textContent = encodeURIComponent(cell.code);

  const editorElement = embed.querySelector<HTMLElement>(
    ISLAND_TAG_NAMES.CODE_EDITOR,
  );
  if (editorElement) {
    editorElement.setAttribute("data-initial-value", JSON.stringify(cell.code));
    const objectId = editorElement.parentElement?.getAttribute(
      OBJECT_ID_ATTR,
    ) as UIElementId | null;
    if (objectId && UI_ELEMENT_REGISTRY.has(objectId)) {
      UI_ELEMENT_REGISTRY.broadcastMessage(
        objectId,
        {
          type: "marimo-ui-value-update",
          value: cell.code,
        },
        [],
      );
    }
  }
}

function islandCellKey(appId: string, cellId: string): string {
  return `${appId}\0${cellId}`;
}

function payloadOccurrenceKey(key: string, occurrence: number): string {
  return `${key}\0${occurrence}`;
}

function payloadMatchKey(
  appId: string,
  cellId: string,
  reactive: boolean,
): string {
  return `${islandCellKey(appId, cellId)}\0${reactive}`;
}
