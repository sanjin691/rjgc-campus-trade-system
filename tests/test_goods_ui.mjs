// Lightweight DOM harness: interaction contracts, not a browser layout test.
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

async function harness() {
  const nodes = new Map();
  class Element {
    constructor() { this.value = ""; this.files = []; this.hidden = true; this.disabled = false; this.children = []; this.listeners = {}; this.classList = { toggle() {} }; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    focus() {}
    reset() { for (const field of Object.values(this.elements)) field.value = ""; nodes.get("goods-files").files = []; }
  }
  const document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    createElement() { return new Element(); },
  };
  const form = document.getElementById("goods-form");
  form.elements = Object.fromEntries(["name", "category", "price", "condition", "description"].map((key) => [key, new Element()]));
  let expire;
  let calls = 0;
  let resolveRequest;
  const api = {
    get: async () => ({ items: [] }),
    request: () => { calls += 1; return new Promise((resolve) => { resolveRequest = resolve; }); },
  };
  const context = vm.createContext({ document, URLSearchParams, location: { search: "" },
    Option: class extends Element { constructor(text, value) { super(); this.text = text; this.value = value; } },
    FileReader: class {}, setTimeout: (callback) => { expire = callback; return 1; }, clearTimeout() {},
  });
  const dependency = new vm.SyntheticModule(["api"], function () { this.setExport("api", api); }, { context });
  const source = new vm.SourceTextModule(readFileSync(new URL("../static/js/goods.js", import.meta.url), "utf8"), { context });
  await source.link(() => dependency);
  await source.evaluate();
  return { nodes, form, page: source.namespace.goodsPage, calls: () => calls,
    expire: () => expire(), resolve: (data) => resolveRequest(data), api };
}

test("ten consecutive submits send one request", async () => {
  const h = await harness();
  h.nodes.get("goods-new").onclick();
  const tasks = Array.from({ length: 10 }, () => h.form.listeners.submit({ preventDefault() {} }));
  await new Promise(setImmediate);
  assert.equal(h.calls(), 1);
  assert.equal(h.nodes.get("goods-save").disabled, true);
  h.resolve({ id: 1, name: "Book", images: [] });
  await Promise.all(tasks);
  assert.equal(h.nodes.get("goods-save").disabled, false);
});

test("timeout preserves inputs and ignores the late response", async () => {
  const h = await harness();
  h.nodes.get("goods-new").onclick();
  h.form.elements.name.value = "Keep my text";
  const task = h.form.listeners.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  h.expire();
  await task;
  assert.match(h.nodes.get("goods-message").textContent, /保存结果未知/);
  h.form.elements.name.value = "New edit";
  h.resolve({ id: 1, name: "Old response", images: [] });
  await new Promise(setImmediate);
  assert.equal(h.form.elements.name.value, "New edit");
  assert.match(h.nodes.get("goods-message").textContent, /保存结果未知/);
});

test("bad image precheck prevents submission", async () => {
  const h = await harness();
  h.nodes.get("goods-files").files = [{ name: "invalid.gif", size: 2097153 }];
  h.nodes.get("goods-files").listeners.change();
  assert.equal(h.nodes.get("goods-save").disabled, true);
  await h.form.listeners.submit({ preventDefault() {} });
  assert.equal(h.calls(), 0);
  assert.match(h.nodes.get("goods-image-errors").textContent, /invalid.gif/);
});

test("expired login keeps current text and offers recovery", async () => {
  const h = await harness();
  h.api.request = async () => { throw Object.assign(new Error("expired"), { status: 401 }); };
  h.form.elements.name.value = "Unsaved book";
  await h.form.listeners.submit({ preventDefault() {} });
  assert.equal(h.form.elements.name.value, "Unsaved book");
  assert.equal(h.nodes.get("goods-recovery").hidden, false);
  assert.match(h.nodes.get("goods-message").textContent, /本次内容未保存/);
});
