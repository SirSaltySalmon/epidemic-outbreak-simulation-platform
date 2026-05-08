/** Thin fetch wrapper. All paths are relative to origin. */

const BASE = "/api/v1";

/** FastAPI `detail` may be a string, object, or validation error list. */
function _detailMessage(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => (e && typeof e === "object" && "msg" in e ? e.msg : JSON.stringify(e)))
      .join("; ");
  }
  return String(detail);
}

export async function get(path) {
  const res = await fetch(BASE + path);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_detailMessage(err.detail) || `GET ${path} → ${res.status}`);
  }
  return res.json();
}

export async function post(path, body) {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_detailMessage(err.detail) || `POST ${path} → ${res.status}`);
  }
  return res.json();
}
