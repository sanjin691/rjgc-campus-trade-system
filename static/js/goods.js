import { api } from "./api.js";

const $ = (id) => document.getElementById(id);
const form = $("goods-form");
const categories = ["图书教材", "数码电子", "生活用品", "服饰配件", "运动用品", "其他"];
const conditions = ["全新", "几乎全新", "轻微使用痕迹", "明显使用痕迹"];
let editing = null;
let kept = [];
let pending = false;
let operation = 0;
let generation = 0;
let opened = false;
let userId = null;
let panel = "home";

function showPanel(next) {
  panel = next;
  $("goods-home").hidden = next !== "home";
  $("goods-mine-panel").hidden = next !== "mine";
  form.hidden = next !== "editor";
  const creating = next === "editor" && !editing;
  const mine = next === "mine" || (next === "editor" && Boolean(editing));
  for (const [id, active] of [["goods-new", creating], ["goods-mine", mine]]) {
    $(id).classList.toggle("is-active", active);
    $(id).setAttribute("aria-pressed", String(active));
  }
}

function closeEditor(destination = "home") {
  ++operation;
  editing = null;
  kept = [];
  form.reset();
  oldImages();
  precheck();
  $("goods-recovery").hidden = true;
  showPanel(destination);
}

async function myGoods() {
  if (pending) return;
  closeEditor("mine");
  message("");
  await list();
}

for (const [key, values] of [["category", categories], ["condition", conditions]]) {
  form.elements[key].append(new Option("请选择", ""), ...values.map((value) => new Option(value, value)));
}

function message(text, success = false) {
  $("goods-message").textContent = text;
  $("goods-message").hidden = !text;
  $("goods-message").classList.toggle("alert-success", success);
}

function recovery(id = editing) {
  $("goods-recovery").hidden = false;
  $("goods-check").href = id ? `/?goodsId=${id}` : "/?goods=1";
}

function lock(value) {
  pending = value;
  $("goods-fields").disabled = value;
  $("goods-cancel").disabled = value;
  $("goods-new").disabled = value;
  $("goods-mine").disabled = value;
  $("goods-refresh").disabled = value;
  $("goods-save").textContent = value ? "正在提交…" : "保存商品";
  precheck();
}

function precheck() {
  const files = [...$("goods-files").files];
  const errors = [];
  $("goods-upload-summary").textContent = files.length
    ? `已选择${files.length}张新增图片：${files.map((file) => file.name).join("、")}`
    : "未选择新增图片";
  if (files.length + kept.length > 3) errors.push(`最多3张，当前共${files.length + kept.length}张，请移除多余图片`);
  files.forEach((file, index) => {
    if (!/\.(jpg|jpeg|png)$/i.test(file.name)) errors.push(`第${index + 1}张 ${file.name}：格式不支持`);
    if (file.size > 2097152) errors.push(`第${index + 1}张 ${file.name}：超过2 MiB`);
  });
  $("goods-image-errors").textContent = errors.join("；");
  $("goods-save").disabled = pending || errors.length > 0;
  return errors.length === 0;
}

function oldImages() {
  $("goods-old-images").replaceChildren();
  for (const image of kept) {
    const box = document.createElement("div");
    const img = document.createElement("img");
    img.src = image.url;
    img.alt = "已保存的商品图片";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "移除此图";
    remove.className = "button button-secondary button-small";
    remove.onclick = () => { kept = kept.filter((item) => item.id !== image.id); oldImages(); precheck(); };
    box.append(img, remove);
    $("goods-old-images").append(box);
  }
}

function editor(item = null) {
  if (pending) return;
  operation += 1;
  editing = item?.id ?? null;
  kept = [...(item?.images ?? [])];
  form.reset();
  for (const key of ["name", "category", "price", "condition", "description"]) {
    form.elements[key].value = item?.[key] ?? "";
  }
  $("goods-form-title").textContent = editing ? `修改商品 #${editing}` : "创建商品";
  $("goods-recovery").hidden = true;
  showPanel("editor");
  message("");
  oldImages();
  precheck();
  form.elements.name.focus();
}

async function edit(id) {
  if (pending) return;
  const epoch = generation;
  const token = ++operation;
  try {
    const item = await api.get(`/api/goods/${id}`);
    if (epoch === generation && token === operation) editor(item);
  } catch (error) {
    if (epoch === generation && token === operation) { message(error.message); if (error.status === 401) recovery(id); }
  }
}

async function list() {
  const epoch = ++generation;
  try {
    const { items } = await api.get("/api/goods");
    if (epoch !== generation) return;
    $("goods-count").textContent = String(items.length);
    const nodes = items.map((item) => {
      const card = document.createElement("article");
      card.className = "panel goods-card";
      const image = document.createElement(item.images.length ? "img" : "div");
      if (item.images.length) { image.src = item.images[0].url; image.alt = item.name; image.loading = "lazy"; }
      else { image.className = "goods-placeholder"; image.textContent = "暂无图片"; }
      const name = document.createElement("h2"); name.textContent = item.name;
      const price = document.createElement("strong"); price.textContent = `¥ ${item.price}`;
      const time = document.createElement("p"); time.textContent = `发布于 ${new Date(item.createdAt).toLocaleString()}`;
      const meta = document.createElement("div"); meta.className = "goods-card-meta";
      meta.textContent = [item.category, item.condition].filter(Boolean).join(" · ");
      const button = document.createElement("button");
      button.type = "button"; button.className = "button button-secondary"; button.textContent = "修改商品";
      button.onclick = () => edit(item.id);
      const remove = document.createElement("button");
      remove.type = "button"; remove.className = "button goods-delete"; remove.textContent = "删除商品";
      remove.onclick = () => deleteGoods(item, remove);
      card.append(image, name, price, meta, time, remove, button);
      return card;
    });
    if (!nodes.length) {
      const empty = document.createElement("div"); empty.className = "goods-empty";
      const title = document.createElement("h3"); title.textContent = "你的好物展台，等待第一件闲置";
      const description = document.createElement("p"); description.textContent = "点击上方“创建商品”，从一本教材、一件闲置开始。";
      empty.append(title, description); nodes.push(empty);
    }
    $("goods-list").replaceChildren(...nodes);
  } catch (error) {
    if (epoch === generation && panel === "mine") { message(error.message); if (error.status === 401) recovery(); }
  }
}

async function deleteGoods(item, button) {
  if (pending || !confirm(`确定删除“${item.name}”？商品及关联图片将永久删除，无法恢复。`)) return;
  const token = ++operation;
  lock(true);
  button.disabled = true;
  button.textContent = "正在删除…";
  message("");
  $("goods-recovery").hidden = true;
  let timer;
  try {
    await Promise.race([
      api.request(`/api/goods/${item.id}`, { method: "DELETE" }),
      new Promise((_, reject) => { timer = setTimeout(() => reject({ unknown: true }), 10000); }),
    ]);
    if (token !== operation) return;
    message("商品删除成功。", true);
    await list();
  } catch (error) {
    if (token !== operation) return;
    if (error.unknown || error.status === 0 || error.status >= 500) {
      message("删除结果未知，请刷新“我的商品”确认，不能据此认定删除失败。");
      recovery(null);
    } else {
      message(error.status === 401 ? "登录已失效，未执行删除。请重新登录后再操作。" : error.message || "删除未完成，请重试。");
      if (error.status === 401) recovery(null);
    }
  } finally {
    clearTimeout(timer);
    if (token === operation) { lock(false); button.disabled = false; button.textContent = "删除商品"; }
  }
}

function encode(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, data: String(reader.result).split(",")[1] });
    reader.onerror = () => reject(new Error(`无法读取图片：${file.name}`));
    reader.onabort = reader.onerror;
    reader.readAsDataURL(file);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending || !precheck()) return;
  const body = {};
  for (const key of ["name", "category", "price", "condition", "description"]) body[key] = form.elements[key].value;
  const files = [...$("goods-files").files];
  const id = editing;
  if (id && !confirm("确定保存本次商品修改？保存后将替换原有商品信息。")) return;
  if (id) body.keepImageIds = kept.map((image) => image.id);
  const token = ++operation;
  lock(true);
  message("");
  $("goods-recovery").hidden = true;
  let timer;
  let sent = false;
  try {
    body.images = await Promise.all(files.map(encode));
    if (token !== operation) return;
    sent = true;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error("timeout"), { unknown: true })), 10000);
    });
    // Promise.race consumes late responses without letting them change the current UI.
    await Promise.race([
      api.request(id ? `/api/goods/${id}` : "/api/goods", { method: id ? "PATCH" : "POST", body }), timeout,
    ]);
    if (token !== operation) return;
    lock(false);
    closeEditor(id ? "mine" : "home");
    message(id ? "商品修改成功。" : "商品创建成功。可点击“创建商品”继续发布，或点击“我的商品”查看。", true);
    if (id) await list();
  } catch (error) {
    if (token !== operation) return;
    if (error.unknown || (sent && (error.status === 0 || error.status >= 500))) {
      message("保存结果未知，请先查看商品确认。重复提交可能产生重复商品或覆盖修改。");
      recovery(id);
    } else {
      const detail = Object.entries(error.errors ?? {}).map(([field, text]) => `${field}：${text}`).join("；");
      message(detail || error.message || "未保存，请稍后重试。");
      if (error.status === 401) { message("登录已失效，本次内容未保存。请在新页面登录，再恢复操作。"); recovery(id); }
    }
  } finally {
    clearTimeout(timer);
    if (token === operation) lock(false);
  }
});

$("goods-files").addEventListener("change", precheck);
$("goods-new").onclick = () => editor();
$("goods-mine").onclick = myGoods;
$("goods-cancel").onclick = async () => {
  if (pending) return;
  const destination = editing ? "mine" : "home";
  closeEditor(destination);
  message("");
  if (destination === "mine") await list();
};
$("goods-refresh").onclick = list;
$("goods-session").onclick = async () => {
  try {
    const session = await api.get("/api/auth/me");
    if (session.user.id !== userId) {
      message("当前登录的是另一个账号，请切回原账号；本页内容不会转交给其他账号。");
      return;
    }
    api.setCsrf(session.csrfToken);
    message("登录已恢复，请核对内容后重新提交。", true);
  } catch (error) { message(error.message); }
};

export const goodsPage = {
  bindUser(id) { userId = id; },
  async open() {
    if (opened) return;
    opened = true;
    const params = new URLSearchParams(location.search);
    const id = params.get("goodsId");
    showPanel(id || params.has("goods") ? "mine" : "home");
    if (panel === "mine") await list();
    if (opened && id && /^[0-9]+$/.test(id)) await edit(Number(id));
  },
  reset() {
    ++operation; ++generation; opened = false; editing = null; kept = []; userId = null;
    form.reset(); showPanel("home"); lock(false); oldImages();
    $("goods-list").replaceChildren(); $("goods-recovery").hidden = true; message("");
    $("goods-count").textContent = "0";
  },
};
