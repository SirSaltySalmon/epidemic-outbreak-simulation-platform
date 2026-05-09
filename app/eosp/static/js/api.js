/** Thin fetch wrapper. All paths are relative to origin. */

const BASE = "/api/v1";

let _getAuthToken = null;

/** Called once after Clerk init (or with () => null when auth is disabled). */
export function setAuthTokenGetter(fn) {
  _getAuthToken = typeof fn === "function" ? fn : null;
}

async function authHeaders() {
  const h = {};
  if (typeof _getAuthToken === "function") {
    try {
      const t = await _getAuthToken();
      if (t) h.Authorization = `Bearer ${t}`;
    } catch (_) {
      /* leave unauthenticated */
    }
  }
  return h;
}

/** FastAPI `detail` may be a string, object, or validation error list. */
function _detailMessage(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail !== null && "message" in detail && typeof detail.message === "string") {
    return detail.message;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((e) => (e && typeof e === "object" && "msg" in e ? e.msg : JSON.stringify(e)))
      .join("; ");
  }
  return String(detail);
}

export async function get(path) {
  const res = await fetch(BASE + path, {
    cache: "no-store",
    headers: await authHeaders(),
    credentials: "include",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_detailMessage(err.detail) || `GET ${path} → ${res.status}`);
  }
  return res.json();
}

export async function post(path, body) {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
    },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg = _detailMessage(err.detail) || `POST ${path} → ${res.status}`;
    const e = new Error(msg);
    e.status = res.status;
    e.body = err;
    throw e;
  }
  return res.json();
}

export async function patch(path, body) {
  const res = await fetch(BASE + path, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
    },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg = _detailMessage(err.detail) || `PATCH ${path} → ${res.status}`;
    const e = new Error(msg);
    e.status = res.status;
    e.body = err;
    throw e;
  }
  return res.json();
}

export async function del(path) {
  const res = await fetch(BASE + path, {
    method: "DELETE",
    headers: await authHeaders(),
    credentials: "include",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_detailMessage(err.detail) || `DELETE ${path} → ${res.status}`);
  }
}
