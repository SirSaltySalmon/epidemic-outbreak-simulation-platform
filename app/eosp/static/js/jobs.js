/**
 * Inference job progress stream (SSE-shaped) over fetch so we can send Bearer tokens.
 * subscribe(jobId, getToken, onEvent, onComplete) → cleanup function
 */

export function subscribe(jobId, getToken, onEvent, onComplete) {
  const ac = new AbortController();
  let finished = false;
  let sawTerminal = false;

  function finish(event) {
    if (finished) return;
    finished = true;
    try {
      ac.abort();
    } catch (_) {
      /* ignore */
    }
    onComplete(event);
  }

  (async () => {
    try {
      const headers = { Accept: "text/event-stream" };
      const token = typeof getToken === "function" ? await getToken() : null;
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`/api/v1/inference/jobs/${encodeURIComponent(jobId)}/events`, {
        headers,
        credentials: "include",
        signal: ac.signal,
      });
      if (!res.ok) {
        const errText = await res.text().catch(() => "");
        finish({
          status: "failed",
          error: `Progress stream HTTP ${res.status}${errText ? `: ${errText.slice(0, 120)}` : ""}`,
        });
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        finish({ status: "failed", error: "No response body" });
        return;
      }
      const decoder = new TextDecoder();
      let buffer = "";
      while (!finished) {
        let chunk;
        try {
          chunk = await reader.read();
        } catch (e) {
          if (ac.signal.aborted || finished) return;
          finish({ status: "failed", error: e && e.message ? e.message : String(e) });
          return;
        }
        const { done, value } = chunk;
        if (done) {
          if (!sawTerminal && !finished) {
            finish({ status: "failed", error: "Progress stream closed before completion" });
          }
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, nl).trimEnd();
          buffer = buffer.slice(nl + 1);
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6);
          try {
            const event = JSON.parse(raw);
            if (event.type === "close" || event.stage === "complete") {
              sawTerminal = true;
              finish(event);
              return;
            }
            onEvent(event);
          } catch (_) {
            /* ignore malformed line */
          }
        }
      }
    } catch (e) {
      if (ac.signal.aborted || finished) return;
      finish({ status: "failed", error: e && e.message ? e.message : String(e) });
    }
  })();

  return () => {
    finished = true;
    try {
      ac.abort();
    } catch (_) {
      /* ignore */
    }
  };
}
