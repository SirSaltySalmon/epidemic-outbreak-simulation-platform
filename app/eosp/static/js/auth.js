/**
 * Clerk bootstrap for the static EOSP dashboard (researcher Console gate).
 */
import { setAuthTokenGetter } from "./api.js";

let _clerk = null;

/** @returns {Promise<string|null>} */
export async function getSessionToken() {
  if (!_clerk?.session) return null;
  try {
    return await _clerk.session.getToken();
  } catch (_) {
    return null;
  }
}

function _bindConsoleVisibility(btnConsole, publishableKeyConfigured) {
  const btn = btnConsole;
  if (!btn) return;

  function sync() {
    if (!publishableKeyConfigured) {
      btn.hidden = false;
      btn.removeAttribute("aria-disabled");
      return;
    }
    const signedIn = !!(_clerk?.user);
    const consoleOk =
      signedIn && _clerk?.user?.publicMetadata?.console_access === true;
    btn.hidden = !consoleOk;
    btn.removeAttribute("aria-disabled");
  }

  if (_clerk && typeof _clerk.addListener === "function") {
    _clerk.addListener(sync);
  }
  sync();
}

function _mountTopbarAuth(wrap, btnConsole, publishableKeyConfigured) {
  if (!wrap || !publishableKeyConfigured || !_clerk) return;

  function render() {
    wrap.innerHTML = "";
    if (_clerk.user) {
      if (typeof _clerk.mountUserButton === "function") {
        const holder = document.createElement("div");
        holder.className = "clerk-user-button-root";
        wrap.appendChild(holder);
        try {
          _clerk.mountUserButton(holder);
        } catch (_) {
          wrap.textContent = _clerk.user.primaryEmailAddress?.emailAddress || "Signed in";
        }
      } else {
        wrap.textContent = _clerk.user.primaryEmailAddress?.emailAddress || "Signed in";
      }
    } else {
      const si = document.createElement("button");
      si.type = "button";
      si.className = "btn-sign-in";
      si.textContent = "Sign in";
      si.addEventListener("click", () => {
        if (typeof _clerk.openSignIn === "function") _clerk.openSignIn();
      });
      wrap.appendChild(si);
    }
  }

  if (typeof _clerk.addListener === "function") _clerk.addListener(render);
  render();
  _bindConsoleVisibility(btnConsole, publishableKeyConfigured);
}

/**
 * Load public config, optionally Clerk; wire token getter for API + SSE.
 * @returns {{ clerkEnabled: boolean }}
 */
export async function initAuth() {
  const btnConsole = document.getElementById("btn-open-drawer");
  const wrap = document.getElementById("clerk-user-wrap");

  let cfg = {};
  try {
    cfg = await fetch("/api/v1/public-config").then((r) => r.json());
  } catch (_) {
    cfg = {};
  }

  const pk = cfg.clerk_publishable_key || null;
  if (!pk) {
    setAuthTokenGetter(() => null);
    _bindConsoleVisibility(btnConsole, false);
    return { clerkEnabled: false };
  }

  try {
    const mod = await import(
      /* webpackIgnore: true */ "https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/+esm"
    );
    const ClerkCtor = mod.Clerk ?? mod.default ?? mod;
    _clerk = new ClerkCtor(pk);
    await _clerk.load();
  } catch (e) {
    console.warn("Clerk failed to load; researcher APIs may return 401.", e);
    setAuthTokenGetter(() => null);
    _bindConsoleVisibility(btnConsole, false);
    return { clerkEnabled: false };
  }

  setAuthTokenGetter(getSessionToken);
  _mountTopbarAuth(wrap, btnConsole, true);
  return { clerkEnabled: true };
}
