import { api, ApiError } from "./api.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  user: null,
  csrf: null,
  tab: "login",
  view: "profile",
  adminUsers: [],
  editingUserId: null,
  resettingUserId: null,
};

const ui = {
  loading: $("#loading-view"),
  authView: $("#auth-view"),
  profileView: $("#profile-view"),
  adminView: $("#admin-view"),
  goodsView: $("#goods-view"),
  tradeView: $("#trade-view"),
  viewNav: $("#view-nav"),
  navAdmin: $("#nav-admin"),
  navProfile: $("#nav-profile"),
  navGoods: $("#nav-goods"),
  navTrade: $("#nav-trade"),
  authTitle: $("#auth-title"),
  authSubtitle: $("#auth-subtitle"),
  authAlert: $("#auth-alert"),
  tabs: $$("[data-tab]"),
  loginForm: $("#login-form"),
  registerForm: $("#register-form"),
  loginSubmit: $("#login-submit"),
  registerSubmit: $("#register-submit"),
  headerUser: $("#header-user"),
  headerRole: $("#header-role"),
  headerUserName: $("#header-user-name"),
  logoutButton: $("#logout-button"),
  profileForm: $("#profile-form"),
  profileAlert: $("#profile-alert"),
  profileSave: $("#profile-save"),
  profileAvatar: $("#profile-avatar"),
  profileDisplayName: $("#profile-display-name"),
  profileDisplayStudentNo: $("#profile-display-studentno"),
  profileStudentNo: $("#profile-studentno"),
  profileCreated: $("#profile-created"),
  profileName: $("#profile-name"),
  profileEmail: $("#profile-email"),
  profilePhone: $("#profile-phone"),
  profileCollege: $("#profile-college"),
  profileMajor: $("#profile-major"),
  passwordForm: $("#password-form"),
  passwordAlert: $("#password-alert"),
  passwordCurrent: $("#password-current"),
  passwordNew: $("#password-new"),
  passwordConfirm: $("#password-confirm"),
  passwordSave: $("#password-save"),
  adminAlert: $("#admin-alert"),
  adminUserCount: $("#admin-user-count"),
  adminUserList: $("#admin-user-list"),
  adminCreateToggle: $("#admin-create-toggle"),
  adminCreatePanel: $("#admin-create-panel"),
  adminCreateForm: $("#admin-create-form"),
  adminCreateStudentNo: $("#admin-create-studentno"),
  adminCreateName: $("#admin-create-name"),
  adminCreateEmail: $("#admin-create-email"),
  adminCreatePassword: $("#admin-create-password"),
  adminCreateConfirm: $("#admin-create-confirm"),
  adminCreateCancel: $("#admin-create-cancel"),
  adminCreateSave: $("#admin-create-save"),
  adminEditPanel: $("#admin-edit-panel"),
  adminEditTarget: $("#admin-edit-target"),
  adminEditForm: $("#admin-edit-form"),
  adminEditStudentNo: $("#admin-edit-studentno"),
  adminEditRole: $("#admin-edit-role"),
  adminEditName: $("#admin-edit-name"),
  adminEditEmail: $("#admin-edit-email"),
  adminEditPhone: $("#admin-edit-phone"),
  adminEditCollege: $("#admin-edit-college"),
  adminEditMajor: $("#admin-edit-major"),
  adminEditCancel: $("#admin-edit-cancel"),
  adminEditSave: $("#admin-edit-save"),
  adminResetPanel: $("#admin-reset-panel"),
  adminResetTarget: $("#admin-reset-target"),
  adminResetForm: $("#admin-reset-form"),
  adminResetPassword: $("#admin-reset-password"),
  adminResetConfirm: $("#admin-reset-confirm"),
  adminResetCancel: $("#admin-reset-cancel"),
  adminResetSave: $("#admin-reset-save"),
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => {
    const map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return map[character];
  });
}

function setAlert(element, message = "", type = "error") {
  if (!element) return;
  element.hidden = !message;
  element.textContent = message;
  element.classList.toggle("alert-success", type === "success");
  element.setAttribute("role", type === "success" ? "status" : "alert");
}

function setLoading(button, loading, text) {
  if (!button) return;
  if (!button.dataset.defaultText) button.dataset.defaultText = button.textContent;
  button.disabled = loading;
  button.textContent = loading ? text : button.dataset.defaultText;
}

function clearFieldErrors(form) {
  if (!form) return;
  $$(".field.has-error", form).forEach((field) => field.classList.remove("has-error"));
  $$(".field-error", form).forEach((element) => {
    element.textContent = "";
  });
  $$("[aria-invalid='true']", form).forEach((element) => element.removeAttribute("aria-invalid"));
}

function showFieldErrors(form, errors = {}) {
  if (!form) return 0;
  let firstInvalid = null;
  let count = 0;
  Object.entries(errors || {}).forEach(([name, message]) => {
    const field = form.querySelector(`[data-field="${name}"]`);
    if (!field) return;
    field.classList.add("has-error");
    const errorElement = $(".field-error", field);
    if (errorElement) errorElement.textContent = message;
    const input = $("input", field);
    if (input) {
      input.setAttribute("aria-invalid", "true");
      if (!firstInvalid) firstInvalid = input;
    }
    count += 1;
  });
  if (firstInvalid) firstInvalid.focus();
  return count;
}

function applyFormError(form, error, alertElement) {
  const mapped = showFieldErrors(form, error?.errors);
  if (mapped === 0) {
    setAlert(alertElement, error?.message || "操作失败，请稍后再试");
  } else {
    setAlert(alertElement, "请检查表单中标出的内容。");
  }
}

function switchTab(tab) {
  state.tab = tab === "register" ? "register" : "login";
  const isRegister = state.tab === "register";

  ui.tabs.forEach((button) => {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  ui.loginForm.hidden = isRegister;
  ui.registerForm.hidden = !isRegister;
  ui.authTitle.textContent = isRegister ? "用户注册" : "用户登录";
  ui.authSubtitle.textContent = isRegister
    ? "填写基本信息，创建系统账号。"
    : "请输入学号或邮箱登录系统。";
  setAlert(ui.authAlert, "");
  clearFieldErrors(ui.loginForm);
  clearFieldErrors(ui.registerForm);
}

function showAuth(tab = "login", message = "", type = "error") {
  state.user = null;
  state.csrf = null;
  state.view = "profile";
  state.adminUsers = [];
  state.editingUserId = null;
  state.resettingUserId = null;
  api.setCsrf(null);

  ui.loading.hidden = true;
  ui.profileView.hidden = true;
  ui.adminView.hidden = true;
  ui.goodsView.hidden = true;
  ui.tradeView.hidden = true;
  ui.viewNav.hidden = true;
  ui.authView.hidden = false;
  ui.headerUser.hidden = true;
  ui.headerRole.hidden = true;
  ui.adminEditPanel.hidden = true;
  ui.adminResetPanel.hidden = true;
  ui.passwordForm.reset();
  setAlert(ui.passwordAlert, "");
  switchTab(tab);
  if (message) setAlert(ui.authAlert, message, type);
  window.setTimeout(() => {
    if (state.tab === "register") $("#register-studentno")?.focus();
    else $("#login-account")?.focus();
  }, 50);
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function roleLabel(role) {
  return role === "admin" ? "管理员" : "普通用户";
}

function updateHeader() {
  if (!state.user) return;
  const isAdmin = state.user.role === "admin";
  ui.headerUser.hidden = false;
  ui.headerRole.hidden = false;
  ui.headerRole.textContent = isAdmin ? "管理员" : "普通用户";
  ui.headerRole.classList.toggle("is-normal", !isAdmin);
  ui.headerUserName.textContent = `${state.user.name}（${state.user.studentNo}）`;
}

function fillProfileFields(user) {
  ui.profileAvatar.textContent = Array.from(user.name || "用户")[0];
  ui.profileDisplayName.textContent = user.name;
  ui.profileDisplayStudentNo.textContent = `学号：${user.studentNo}`;
  ui.profileStudentNo.value = user.studentNo;
  ui.profileCreated.value = formatDate(user.createdAt);
  ui.profileName.value = user.name;
  ui.profileEmail.value = user.email;
  ui.profilePhone.value = user.phone || "";
  ui.profileCollege.value = user.college || "";
  ui.profileMajor.value = user.major || "";
}

function showApp(data) {
  state.user = data.user;
  state.csrf = data.csrfToken;
  api.setCsrf(data.csrfToken);

  ui.loading.hidden = true;
  ui.authView.hidden = true;
  ui.headerUser.hidden = false;
  setAlert(ui.profileAlert, "");
  clearFieldErrors(ui.profileForm);
  updateHeader();
  fillProfileFields(data.user);

  const isAdmin = data.user.role === "admin";
  // 子系统导航对所有登录用户可见；「用户管理」仅管理员可用
  ui.viewNav.hidden = false;
  ui.navAdmin.hidden = !isAdmin;
  switchView(isAdmin ? "admin" : "profile");
}

const VIEWS = () => ({
  admin: ui.adminView,
  profile: ui.profileView,
  goods: ui.goodsView,
  trade: ui.tradeView,
});

const NAV_BUTTONS = () => ({
  admin: ui.navAdmin,
  profile: ui.navProfile,
  goods: ui.navGoods,
  trade: ui.navTrade,
});

function switchView(view) {
  if (view === "admin" && state.user?.role !== "admin") return;
  const views = VIEWS();
  if (!views[view]) return;
  state.view = view;

  Object.entries(views).forEach(([key, element]) => {
    if (element) element.hidden = key !== view;
  });

  Object.entries(NAV_BUTTONS()).forEach(([key, button]) => {
    if (!button) return;
    const active = key === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  ui.viewNav.classList.toggle("is-profile", view === "profile");

  if (view === "admin") {
    loadAdminUsers();
  } else if (view === "profile") {
    closeAdminCreate();
    closeAdminEdit();
    fillProfileFields(state.user);
    setAlert(ui.profileAlert, "");
    setAlert(ui.passwordAlert, "");
  }
}

function roleBadgeHtml(role) {
  return `<span class="role-badge${role === "admin" ? "" : " is-normal"}">${roleLabel(role)}</span>`;
}

function userCardHtml(user) {
  return `
    <article class="user-card">
      <div class="user-card-head">
        <div class="user-card-name">
          <strong>${escapeHtml(user.name)}</strong>
          ${roleBadgeHtml(user.role)}
        </div>
        <div class="user-card-actions">
          <button class="button button-secondary button-small" type="button" data-edit-user="${user.id}">编辑</button>
          <button class="button button-secondary button-small" type="button" data-reset-user="${user.id}">重置密码</button>
          <button class="button button-danger button-small" type="button" data-delete-user="${user.id}">删除</button>
        </div>
      </div>
      <dl class="user-info">
        <div><dt>学号 / 工号</dt><dd>${escapeHtml(user.studentNo)}</dd></div>
        <div><dt>注册时间</dt><dd>${escapeHtml(formatDate(user.createdAt))}</dd></div>
        <div class="user-info-full"><dt>邮箱</dt><dd>${escapeHtml(user.email)}</dd></div>
        <div><dt>手机号</dt><dd>${escapeHtml(user.phone || "未填写")}</dd></div>
        <div><dt>学院</dt><dd>${escapeHtml(user.college || "未填写")}</dd></div>
        <div class="user-info-full"><dt>专业</dt><dd>${escapeHtml(user.major || "未填写")}</dd></div>
      </dl>
    </article>
  `;
}

function renderAdminUsers() {
  ui.adminUserCount.textContent = `共 ${state.adminUsers.length} 位用户`;
  if (!state.adminUsers.length) {
    ui.adminUserList.innerHTML = '<p class="loading-inline">暂无用户。</p>';
    return;
  }
  ui.adminUserList.innerHTML = state.adminUsers.map(userCardHtml).join("");
}

async function loadAdminUsers() {
  ui.adminAlert.hidden = true;
  ui.adminUserList.innerHTML = '<p class="loading-inline">正在加载用户信息…</p>';
  try {
    const data = await api.get("/api/admin/users");
    if (state.view !== "admin") return;
    state.adminUsers = Array.isArray(data.items) ? data.items : [];
    renderAdminUsers();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      handleExpiredSession();
      return;
    }
    ui.adminUserList.innerHTML = '<p class="loading-inline">用户信息加载失败。</p>';
    setAlert(ui.adminAlert, error?.message || "用户信息加载失败，请稍后再试。");
  }
}

function openAdminEdit(userId) {
  const user = state.adminUsers.find((item) => item.id === userId);
  if (!user) return;
  closeAdminCreate();
  closeAdminReset();
  state.editingUserId = userId;
  clearFieldErrors(ui.adminEditForm);
  setAlert(ui.adminAlert, "");
  ui.adminEditTarget.textContent = `${user.name}（${user.studentNo}）`;
  ui.adminEditStudentNo.value = user.studentNo;
  ui.adminEditRole.value = roleLabel(user.role);
  ui.adminEditName.value = user.name;
  ui.adminEditEmail.value = user.email;
  ui.adminEditPhone.value = user.phone || "";
  ui.adminEditCollege.value = user.college || "";
  ui.adminEditMajor.value = user.major || "";
  ui.adminEditPanel.hidden = false;
  ui.adminEditName.focus();
  ui.adminEditPanel.scrollIntoView({ block: "nearest" });
}

function closeAdminEdit() {
  state.editingUserId = null;
  ui.adminEditPanel.hidden = true;
  clearFieldErrors(ui.adminEditForm);
}

function toggleAdminCreate() {
  const willOpen = ui.adminCreatePanel.hidden;
  ui.adminCreatePanel.hidden = !willOpen;
  clearFieldErrors(ui.adminCreateForm);
  setAlert(ui.adminAlert, "");
  if (willOpen) {
    closeAdminEdit();
    closeAdminReset();
    ui.adminCreateStudentNo.focus();
    ui.adminCreatePanel.scrollIntoView({ block: "nearest" });
  }
}

function closeAdminCreate() {
  ui.adminCreatePanel.hidden = true;
  clearFieldErrors(ui.adminCreateForm);
  ui.adminCreateForm.reset();
}

async function createAdmin(event) {
  event.preventDefault();
  clearFieldErrors(ui.adminCreateForm);
  setAlert(ui.adminAlert, "");

  const payload = {
    studentNo: ui.adminCreateStudentNo.value.trim(),
    name: ui.adminCreateName.value.trim(),
    email: ui.adminCreateEmail.value.trim(),
    password: ui.adminCreatePassword.value,
    confirmPassword: ui.adminCreateConfirm.value,
  };

  setLoading(ui.adminCreateSave, true, "创建中…");
  try {
    const data = await api.post("/api/admin/users", payload);
    closeAdminCreate();
    await loadAdminUsers();
    setAlert(ui.adminAlert, `管理员账号 ${data.user.studentNo} 已创建。`, "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      handleExpiredSession();
      return;
    }
    applyFormError(ui.adminCreateForm, error, ui.adminAlert);
  } finally {
    setLoading(ui.adminCreateSave, false);
  }
}

async function saveAdminEdit(event) {
  event.preventDefault();
  if (!state.editingUserId) return;
  clearFieldErrors(ui.adminEditForm);
  setAlert(ui.adminAlert, "");

  const payload = {
    studentNo: ui.adminEditStudentNo.value.trim(),
    name: ui.adminEditName.value.trim(),
    email: ui.adminEditEmail.value.trim(),
    phone: ui.adminEditPhone.value.trim(),
    college: ui.adminEditCollege.value.trim(),
    major: ui.adminEditMajor.value.trim(),
  };

  setLoading(ui.adminEditSave, true, "保存中…");
  try {
    const data = await api.patch(`/api/admin/users/${state.editingUserId}`, payload);
    if (data.user.id === state.user.id) {
      state.user = data.user;
      updateHeader();
      fillProfileFields(data.user);
    }
    closeAdminEdit();
    await loadAdminUsers();
    setAlert(ui.adminAlert, "用户信息已保存。", "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      handleExpiredSession();
      return;
    }
    applyFormError(ui.adminEditForm, error, ui.adminAlert);
  } finally {
    setLoading(ui.adminEditSave, false);
  }
}

function openAdminReset(userId) {
  const user = state.adminUsers.find((item) => item.id === userId);
  if (!user) return;
  state.resettingUserId = userId;
  closeAdminEdit();
  closeAdminCreate();
  clearFieldErrors(ui.adminResetForm);
  ui.adminResetForm.reset();
  setAlert(ui.adminAlert, "");
  ui.adminResetTarget.textContent = `${user.name}（${user.studentNo}）`;
  ui.adminResetPanel.hidden = false;
  ui.adminResetPassword.focus();
  ui.adminResetPanel.scrollIntoView({ block: "nearest" });
}

function closeAdminReset() {
  state.resettingUserId = null;
  ui.adminResetPanel.hidden = true;
  clearFieldErrors(ui.adminResetForm);
  ui.adminResetForm.reset();
}

async function submitAdminReset(event) {
  event.preventDefault();
  if (!state.resettingUserId) return;
  clearFieldErrors(ui.adminResetForm);
  setAlert(ui.adminAlert, "");

  const target = state.adminUsers.find((item) => item.id === state.resettingUserId);
  const payload = {
    newPassword: ui.adminResetPassword.value,
    confirmPassword: ui.adminResetConfirm.value,
  };

  // 二次确认：重置密码会让该用户所有登录失效，属于不可撤回的高危操作
  const confirmed = window.confirm(
    `确定要重置「${target?.name || "该用户"}（${target?.studentNo || ""}）」的密码吗？\n`
      + "重置后该用户所有登录状态立即失效，需用新密码重新登录。",
  );
  if (!confirmed) return;

  setLoading(ui.adminResetSave, true, "重置中…");
  try {
    await api.post(`/api/admin/users/${state.resettingUserId}/password`, payload);
    closeAdminReset();
    setAlert(ui.adminAlert, `已重置 ${target?.studentNo || "该用户"} 的密码，请通知其使用新密码登录。`, "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      handleExpiredSession();
      return;
    }
    applyFormError(ui.adminResetForm, error, ui.adminAlert);
  } finally {
    setLoading(ui.adminResetSave, false);
  }
}

async function deleteUser(userId) {
  const user = state.adminUsers.find((item) => item.id === userId);
  if (!user) return;

  // 高危且不可逆：要求输入该用户的学号/工号，避免误点或批量误删
  const typed = window.prompt(
    `删除不可恢复。\n请输入该用户的学号/工号「${user.studentNo}」以确认删除：`,
    "",
  );
  if (typed === null) return;
  if (typed.trim().toUpperCase() !== user.studentNo.toUpperCase()) {
    setAlert(ui.adminAlert, "学号/工号不一致，已取消删除。");
    return;
  }

  setAlert(ui.adminAlert, "");
  try {
    await api.delete(`/api/admin/users/${userId}`);
    if (state.editingUserId === userId) closeAdminEdit();
    if (state.resettingUserId === userId) closeAdminReset();
    await loadAdminUsers();
    setAlert(ui.adminAlert, `已删除用户 ${user.studentNo}。`, "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      handleExpiredSession();
      return;
    }
    setAlert(ui.adminAlert, error?.message || "删除失败，请稍后再试。");
  }
}

async function changePassword(event) {
  event.preventDefault();
  clearFieldErrors(ui.passwordForm);
  setAlert(ui.passwordAlert, "");

  const payload = {
    currentPassword: ui.passwordCurrent.value,
    newPassword: ui.passwordNew.value,
    confirmPassword: ui.passwordConfirm.value,
  };

  setLoading(ui.passwordSave, true, "保存中…");
  try {
    await api.patch("/api/users/me/password", payload);
    ui.passwordForm.reset();
    setAlert(ui.passwordAlert, "密码已修改，其他设备上的登录已失效。", "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      handleExpiredSession();
      return;
    }
    applyFormError(ui.passwordForm, error, ui.passwordAlert);
  } finally {
    setLoading(ui.passwordSave, false);
  }
}

function handleExpiredSession() {
  showAuth("login", "登录状态已失效，请重新登录。");
}

async function login(event) {
  event.preventDefault();
  setAlert(ui.authAlert, "");
  clearFieldErrors(ui.loginForm);

  const payload = {
    account: $("#login-account").value.trim(),
    password: $("#login-password").value,
  };

  setLoading(ui.loginSubmit, true, "登录中…");
  try {
    const data = await api.post("/api/auth/login", payload);
    ui.loginForm.reset();
    showApp(data);
  } catch (error) {
    applyFormError(ui.loginForm, error, ui.authAlert);
  } finally {
    setLoading(ui.loginSubmit, false);
  }
}

async function register(event) {
  event.preventDefault();
  setAlert(ui.authAlert, "");
  clearFieldErrors(ui.registerForm);

  const form = ui.registerForm;
  const payload = {
    studentNo: $("#register-studentno").value.trim(),
    name: $("#register-name").value.trim(),
    email: $("#register-email").value.trim(),
    phone: $("#register-phone").value.trim(),
    college: $("#register-college").value.trim(),
    major: $("#register-major").value.trim(),
    password: $("#register-password").value,
    confirmPassword: $("#register-confirm").value,
  };

  setLoading(ui.registerSubmit, true, "注册中…");
  try {
    const data = await api.post("/api/auth/register", payload);
    form.reset();
    switchTab("login");
    ui.loginForm.reset();
    $("#login-account").value = data.studentNo;
    setAlert(ui.authAlert, "注册成功，请使用学号或邮箱登录。", "success");
    $("#login-password").focus();
  } catch (error) {
    applyFormError(ui.registerForm, error, ui.authAlert);
  } finally {
    setLoading(ui.registerSubmit, false);
  }
}

async function saveProfile(event) {
  event.preventDefault();
  setAlert(ui.profileAlert, "");
  clearFieldErrors(ui.profileForm);

  const payload = {
    name: ui.profileName.value.trim(),
    email: ui.profileEmail.value.trim(),
    phone: ui.profilePhone.value.trim(),
    college: ui.profileCollege.value.trim(),
    major: ui.profileMajor.value.trim(),
  };

  setLoading(ui.profileSave, true, "保存中…");
  try {
    const data = await api.patch("/api/users/me", payload);
    state.user = data.user;
    updateHeader();
    fillProfileFields(data.user);
    setAlert(ui.profileAlert, "个人信息已保存。", "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      handleExpiredSession();
      return;
    }
    applyFormError(ui.profileForm, error, ui.profileAlert);
  } finally {
    setLoading(ui.profileSave, false);
  }
}

async function logout() {
  ui.logoutButton.disabled = true;
  try {
    await api.post("/api/auth/logout");
    ui.loginForm.reset();
    ui.registerForm.reset();
    ui.profileForm.reset();
    ui.passwordForm.reset();
    closeAdminCreate();
    closeAdminEdit();
    closeAdminReset();
    showAuth("login", "已退出登录。", "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showAuth("login", "已退出登录。", "success");
    } else {
      setAlert(ui.profileAlert, error?.message || "退出失败，请稍后重试。");
      setAlert(ui.adminAlert, error?.message || "退出失败，请稍后重试。");
    }
  } finally {
    ui.logoutButton.disabled = false;
  }
}

function bindEvents() {
  ui.tabs.forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  $$("[data-tab-target]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tabTarget));
  });

  ui.navAdmin.addEventListener("click", () => switchView("admin"));
  ui.navProfile.addEventListener("click", () => switchView("profile"));
  ui.navGoods.addEventListener("click", () => switchView("goods"));
  ui.navTrade.addEventListener("click", () => switchView("trade"));

  ui.adminUserList.addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-edit-user]");
    if (editButton) {
      openAdminEdit(Number(editButton.dataset.editUser));
      return;
    }
    const resetButton = event.target.closest("[data-reset-user]");
    if (resetButton) {
      openAdminReset(Number(resetButton.dataset.resetUser));
      return;
    }
    const deleteButton = event.target.closest("[data-delete-user]");
    if (deleteButton) {
      deleteUser(Number(deleteButton.dataset.deleteUser));
    }
  });
  ui.adminEditCancel.addEventListener("click", closeAdminEdit);
  ui.adminEditForm.addEventListener("submit", saveAdminEdit);

  ui.adminResetCancel.addEventListener("click", closeAdminReset);
  ui.adminResetForm.addEventListener("submit", submitAdminReset);

  ui.passwordForm.addEventListener("submit", changePassword);

  ui.adminCreateToggle.addEventListener("click", toggleAdminCreate);
  ui.adminCreateCancel.addEventListener("click", closeAdminCreate);
  ui.adminCreateForm.addEventListener("submit", createAdmin);

  ui.loginForm.addEventListener("submit", login);
  ui.registerForm.addEventListener("submit", register);
  ui.profileForm.addEventListener("submit", saveProfile);
  ui.logoutButton.addEventListener("click", logout);

  $$("form").forEach((form) => {
    form.addEventListener("input", (event) => {
      const field = event.target.closest(".field");
      if (field?.classList.contains("has-error")) {
        field.classList.remove("has-error");
        const message = $(".field-error", field);
        if (message) message.textContent = "";
        event.target.removeAttribute("aria-invalid");
      }
      if (form === ui.loginForm || form === ui.registerForm) setAlert(ui.authAlert, "");
      if (form === ui.profileForm) setAlert(ui.profileAlert, "");
      if (form === ui.passwordForm) setAlert(ui.passwordAlert, "");
      if (form === ui.adminEditForm || form === ui.adminCreateForm || form === ui.adminResetForm) {
        setAlert(ui.adminAlert, "");
      }
    });
  });
}

async function init() {
  bindEvents();
  try {
    const data = await api.get("/api/auth/me");
    showApp(data);
  } catch (error) {
    const message = error?.status !== 401
      ? "服务暂时不可用，请稍后刷新页面。"
      : "";
    showAuth("login", message);
  }
}

init();
