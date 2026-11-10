// DOM-only layout verification through the Edge DevTools Protocol.
// No screenshots are captured. Usage: node tools/check_layout.mjs
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9333;
const URL = "http://127.0.0.1:8789/?layout=1";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForJson(url, timeoutMs = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
    } catch {
      // Edge is still starting.
    }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (!payload.id) return;
      const handler = this.pending.get(payload.id);
      if (!handler) return;
      this.pending.delete(payload.id);
      if (payload.error) handler.reject(new Error(payload.error.message));
      else handler.resolve(payload.result);
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
  }
}

async function evaluate(client, expression) {
  const result = await client.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Evaluation failed");
  }
  return result.result.value;
}

async function collect(client, width, height) {
  await client.send("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 600,
  });
  await client.send("Page.navigate", { url: URL });
  const started = Date.now();
  let report = null;
  while (Date.now() - started < 12000) {
    report = await evaluate(
      client,
      "document.querySelector('#layoutReport')?.textContent || null",
    );
    if (report) break;
    await sleep(250);
  }
  if (!report) throw new Error(`Layout report missing at ${width}px`);
  const details = await evaluate(
    client,
    `({
      layout: ${JSON.stringify(report)},
      innerWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      indicatorRows: document.querySelectorAll('.indicator-row').length,
      researchSteps: document.querySelectorAll('.research-step').length,
      poolSymbols: document.querySelector('#poolSymbolsCount')?.textContent,
      poolRows: document.querySelector('#poolRowsCount')?.textContent,
      poolDateRange: document.querySelector('#poolDateRange')?.textContent,
      reportTitle: document.querySelector('#reportTitle')?.textContent,
    })`,
  );
  return { requestedWidth: width, ...details };
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "qg-edge-"));
const child = spawn(
  EDGE,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: "ignore", windowsHide: true },
);

try {
  const targets = await waitForJson(`http://127.0.0.1:${PORT}/json/list`);
  const page = targets.find((item) => item.type === "page");
  if (!page) throw new Error("No page target from Edge");
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const client = new Cdp(socket);
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  const desktop = await collect(client, 1440, 1200);
  const interaction = await evaluate(
    client,
    `(async () => {
      const before = document.querySelector('#reportIdentity h3')?.textContent || '';
      const tabs = Array.from(document.querySelectorAll('.stock-tab'));
      tabs[1]?.click();
      await new Promise((resolve) => setTimeout(resolve, 150));
      const after = document.querySelector('#reportIdentity h3')?.textContent || '';
      document.querySelector('[data-tab="news"]')?.click();
      await new Promise((resolve) => setTimeout(resolve, 150));
      return {
        before,
        after,
        newsRows: document.querySelectorAll('.news-row').length,
        datasetOptions: document.querySelectorAll('#datasetSelect option').length,
        defaultPoolSize: document.querySelector('#universeSize')?.value,
        defaultHistoryDays: document.querySelector('#poolHistoryDays')?.value,
        poolOptions: Array.from(document.querySelectorAll('#universeSize option')).map((item) => item.value),
        historyOptions: Array.from(document.querySelectorAll('#poolHistoryDays option')).map((item) => item.value),
      };
    })()`,
  );
  const mobile = await collect(client, 390, 1200);
  const result = { desktop, interaction, mobile };
  console.log(JSON.stringify(result, null, 2));
  const desktopLayout = JSON.parse(desktop.layout);
  const mobileLayout = JSON.parse(mobile.layout);
  const failed =
    desktop.indicatorRows !== 14 ||
    interaction.defaultHistoryDays !== '1000' ||
    !interaction.historyOptions.includes('5000') ||
    Number(desktop.poolSymbols.replaceAll(',', '')) < 2800 ||
    Number(desktop.poolRows.replaceAll(',', '')) < 2800000 ||
    !String(desktop.poolDateRange || '').includes('2022') ||
    interaction.defaultPoolSize !== '3000' ||
    !interaction.poolOptions.includes('3000') ||
    !interaction.poolOptions.includes('5000') ||
    desktop.researchSteps !== 6 ||
    interaction.before === interaction.after ||
    interaction.newsRows < 1 ||
    interaction.datasetOptions < 1 ||
    mobile.indicatorRows !== 14 ||
    mobile.researchSteps !== 6 ||
    desktopLayout.overflowEls.length ||
    mobileLayout.overflowEls.length ||
    desktopLayout.overlaps.length ||
    mobileLayout.overlaps.length;
  socket.close();
  if (failed) process.exitCode = 1;
} finally {
  child.kill();
  await new Promise((resolve) => {
    child.once("exit", resolve);
    setTimeout(resolve, 2000);
  });
  if (profile.startsWith(os.tmpdir()) && path.basename(profile).startsWith("qg-edge-")) {
    try {
      fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    } catch {
      // Edge may briefly retain its profile lock after exit.
    }
  }
}
