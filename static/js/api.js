const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(message, { status = 0, errors = {} } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errors = errors || {};
  }
}

class ApiClient {
  constructor() {
    this.csrfToken = null;
  }

  setCsrf(token) {
    this.csrfToken = token || null;
  }

  async request(path, { method = "GET", body } = {}) {
    const headers = { Accept: "application/json" };
    const options = { method, headers, credentials: "same-origin" };

    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    if (this.csrfToken && MUTATING_METHODS.has(method)) {
      headers["X-CSRF-Token"] = this.csrfToken;
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new ApiError("无法连接服务器，请稍后再试", { status: 0 });
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok || !payload || payload.ok !== true) {
      throw new ApiError(payload?.message || `请求失败（${response.status}）`, {
        status: response.status,
        errors: payload?.errors || {},
      });
    }

    return payload.data;
  }

  get(path) {
    return this.request(path);
  }

  post(path, body = {}) {
    return this.request(path, { method: "POST", body });
  }

  patch(path, body = {}) {
    return this.request(path, { method: "PATCH", body });
  }

  delete(path) {
    return this.request(path, { method: "DELETE" });
  }
}

export const api = new ApiClient();
