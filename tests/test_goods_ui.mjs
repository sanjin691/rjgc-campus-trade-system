// Lightweight DOM harness: interaction contracts, not a browser layout test.
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

async function harness(confirmed = true) {
  const nodes = new Map();
  class Element {
    constructor() { this.value = ""; this.files = []; this.hidden = true; this.disabled = false; this.children = []; this.listeners = {}; this.attributes = {}; this.classes = new Set(); this.classList = { toggle: (name, active) => active ? this.classes.add(name) : this.classes.delete(name) }; }
    setAttribute(name, value) { this.attributes[name] = value; }
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
  const confirmations = [];
  const api = {
    get: async () => ({ items: [] }),
    request: () => { calls += 1; return new Promise((resolve) => { resolveRequest = resolve; }); },
  };
  const context = vm.createContext({ document, URLSearchParams, location: { search: "" }, confirm: (text) => { confirmations.push(text); return confirmed; },
    Option: class extends Element { constructor(text, value) { super(); this.text = text; this.value = value; } },
    FileReader: class {}, setTimeout: (callback) => { expire = callback; return 1; }, clearTimeout() {},
  });
  const dependency = new vm.SyntheticModule(["api"], function () { this.setExport("api", api); }, { context });
  const source = new vm.SourceTextModule(readFileSync(new URL("../static/js/goods.js", import.meta.url), "utf8"), { context });
  await source.link(() => dependency);
  await source.evaluate();
  return { nodes, form, page: source.namespace.goodsPage, calls: () => calls,
    expire: () => expire(), resolve: (data) => resolveRequest(data), api, confirmations };
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

test("creation exits the form without displaying the list; another creation starts empty", async () => {
  const h = await harness();
  h.nodes.get("goods-new").onclick();
  h.form.elements.name.value = "First book";
  assert.equal(h.form.hidden, false);
  assert.equal(h.nodes.get("goods-mine-panel").hidden, true);
  const task = h.form.listeners.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  h.resolve({ id: 1, name: "First book", images: [] });
  await task;
  assert.equal(h.form.hidden, true);
  assert.equal(h.nodes.get("goods-home").hidden, false);
  assert.equal(h.nodes.get("goods-mine-panel").hidden, true);
  assert.match(h.nodes.get("goods-message").textContent, /商品创建成功/);
  h.nodes.get("goods-new").onclick();
  assert.equal(h.form.hidden, false);
  assert.equal(h.form.elements.name.value, "");
  assert.equal(h.nodes.get("goods-form-title").textContent, "创建商品");
  assert.equal(h.nodes.get("goods-mine-panel").hidden, true);
});

test("my goods provides editing on a separate view and returns to the list after saving", async () => {
  const h = await harness();
  let item = { id: 12, name: "Book", category: "图书教材", condition: "全新", price: "10.00", description: "No notes", images: [], createdAt: "2026-11-08T00:00:00Z" };
  h.api.get = async (path) => path === "/api/goods" ? { items: [item] } : item;
  await h.nodes.get("goods-mine").onclick();
  assert.equal(h.form.hidden, true);
  assert.equal(h.nodes.get("goods-mine-panel").hidden, false);
  const card = h.nodes.get("goods-list").children[0];
  await card.children.at(-1).onclick();
  assert.equal(h.form.hidden, false);
  assert.equal(h.nodes.get("goods-mine-panel").hidden, true);
  assert.equal(h.form.elements.name.value, "Book");
  h.form.elements.price.value = "15";
  const task = h.form.listeners.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  item = { ...item, price: "15.00" };
  h.resolve(item);
  await task;
  assert.equal(h.form.hidden, true);
  assert.equal(h.nodes.get("goods-mine-panel").hidden, false);
  assert.equal(h.nodes.get("goods-list").children[0].children[2].textContent, "¥ 15.00");
  assert.match(h.nodes.get("goods-message").textContent, /商品修改成功/);
});

test("failed creation preserves the form and never exposes the list", async () => {
  const h = await harness();
  h.nodes.get("goods-new").onclick();
  h.form.elements.name.value = "Unsaved book";
  h.api.request = async () => { throw Object.assign(new Error("Invalid price"), { status: 422 }); };
  await h.form.listeners.submit({ preventDefault() {} });
  assert.equal(h.form.hidden, false);
  assert.equal(h.form.elements.name.value, "Unsaved book");
  assert.equal(h.nodes.get("goods-mine-panel").hidden, true);
  assert.equal(h.nodes.get("goods-save").disabled, false);
});

test("cancel creation exits to home while cancel modification returns to my goods", async () => {
  const h = await harness();
  h.nodes.get("goods-new").onclick();
  await h.nodes.get("goods-cancel").onclick();
  assert.equal(h.form.hidden, true);
  assert.equal(h.nodes.get("goods-home").hidden, false);
  const item = { id: 2, name: "Book", price: "1.00", images: [] };
  h.api.get = async (path) => path === "/api/goods" ? { items: [item] } : item;
  await h.nodes.get("goods-mine").onclick();
  await h.nodes.get("goods-list").children[0].children.at(-1).onclick();
  await h.nodes.get("goods-cancel").onclick();
  assert.equal(h.form.hidden, true);
  assert.equal(h.nodes.get("goods-mine-panel").hidden, false);
});

test("equal navigation controls indicate creation, list and editing selection", async () => {
  const h = await harness();
  await h.page.open();
  const create = h.nodes.get("goods-new"), mine = h.nodes.get("goods-mine");
  assert.equal(create.attributes["aria-pressed"], "false");
  assert.equal(mine.attributes["aria-pressed"], "false");
  create.onclick();
  assert.equal(create.classes.has("is-active"), true);
  assert.equal(mine.classes.has("is-active"), false);
  const item = { id: 2, name: "Book", images: [], price: "10.00" };
  h.api.get = async (path) => path === "/api/goods" ? { items: [item] } : item;
  await mine.onclick();
  assert.equal(create.attributes["aria-pressed"], "false");
  assert.equal(mine.attributes["aria-pressed"], "true");
  assert.equal(h.nodes.get("goods-count").textContent, "1");
  await h.nodes.get("goods-list").children[0].children.at(-1).onclick();
  assert.equal(mine.classes.has("is-active"), true);
  assert.equal(create.classes.has("is-active"), false);
});

async function deleteFixture(confirmed = true) {
  const h = await harness(confirmed);
  h.api.get = async () => ({ items: [{ id: 7, name: 'Book', price: '1.00', images: [] }] });
  await h.nodes.get('goods-mine').onclick();
  h.remove = h.nodes.get('goods-list').children[0].children.at(-2);
  return h;
}

test('cancel deletion sends no request', async () => {
  const h = await deleteFixture(false);
  await h.remove.onclick();
  assert.equal(h.calls(), 0);
  assert.equal(h.nodes.get('goods-count').textContent, '1');
});

test('confirmed deletion locks repeated clicks and refreshes list', async () => {
  const h = await deleteFixture();
  const task = h.remove.onclick();
  await h.remove.onclick();
  assert.equal(h.calls(), 1);
  assert.equal(h.remove.disabled, true);
  h.api.get = async () => ({ items: [] });
  h.resolve(null);
  await task;
  assert.equal(h.nodes.get('goods-count').textContent, '0');
  assert.match(h.nodes.get('goods-message').textContent, /删除成功/);
});

test('deletion timeout reports unknown and ignores a late response', async () => {
  const h = await deleteFixture();
  const task = h.remove.onclick();
  h.expire();
  await task;
  assert.match(h.nodes.get('goods-message').textContent, /删除结果未知/);
  assert.equal(h.remove.disabled, false);
  h.resolve(null);
  await new Promise(setImmediate);
  assert.match(h.nodes.get('goods-message').textContent, /删除结果未知/);
  assert.equal(h.nodes.get('goods-count').textContent, '1');
});

test('cancel modification confirmation keeps inputs and sends no request', async () => {
  const h = await harness(false);
  const item = { id: 12, name: 'Book', price: '10.00', images: [] };
  h.api.get = async path => path === '/api/goods' ? { items: [item] } : item;
  await h.nodes.get('goods-mine').onclick();
  await h.nodes.get('goods-list').children[0].children.at(-1).onclick();
  h.form.elements.price.value = '15';
  await h.form.listeners.submit({ preventDefault() {} });
  assert.equal(h.calls(), 0);
  assert.equal(h.form.hidden, false);
  assert.equal(h.form.elements.price.value, '15');
  assert.equal(h.nodes.get('goods-save').disabled, false);
  assert.match(h.confirmations[0], /确定保存本次商品修改/);
});

test('accept modification confirmation saves exactly once; creation does not confirm', async () => {
  const h = await harness(true);
  const item = { id: 12, name: 'Book', price: '10.00', images: [] };
  h.api.get = async path => path === '/api/goods' ? { items: [item] } : item;
  await h.nodes.get('goods-mine').onclick();
  await h.nodes.get('goods-list').children[0].children.at(-1).onclick();
  const saving = h.form.listeners.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  await h.form.listeners.submit({ preventDefault() {} });
  assert.equal(h.calls(), 1);
  assert.equal(h.confirmations.length, 1);
  h.resolve(item);
  await saving;
  h.nodes.get('goods-new').onclick();
  const creating = h.form.listeners.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  assert.equal(h.confirmations.length, 1);
  h.resolve({ ...item, id: 13 });
  await creating;
});
