/**
 * SSE client for inference job progress.
 * subscribe(jobId, onEvent, onClose) → returns a cleanup function.
 */

export function subscribe(jobId, onEvent, onClose) {
  const url = `/api/v1/inference/jobs/${jobId}/events`;
  const source = new EventSource(url);
  let finished = false;

  function finish(event) {
    if (finished) return;
    finished = true;
    source.close();
    onClose(event);
  }

  source.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      if (event.type === "close" || event.stage === "complete") {
        finish(event);
      } else {
        onEvent(event);
      }
    } catch (_) { /* ignore malformed */ }
  };

  source.onerror = () => {
    // Browsers can fire transient EventSource errors while reconnecting. Only
    // surface a terminal failure if the stream is actually closed before the
    // server sent a complete event.
    if (source.readyState === EventSource.CLOSED) {
      finish({ status: "failed", error: "Progress stream closed before completion" });
    }
  };

  return () => {
    finished = true;
    source.close();
  };
}
