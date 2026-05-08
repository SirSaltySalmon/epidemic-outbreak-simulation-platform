/**
 * SSE client for inference job progress.
 * subscribe(jobId, onEvent, onClose) → returns a cleanup function.
 */

export function subscribe(jobId, onEvent, onClose) {
  const url = `/api/v1/inference/jobs/${jobId}/events`;
  const source = new EventSource(url);

  source.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      if (event.type === "close" || event.stage === "complete") {
        onClose(event);
        source.close();
      } else {
        onEvent(event);
      }
    } catch (_) { /* ignore malformed */ }
  };

  source.onerror = () => {
    onClose({ error: true });
    source.close();
  };

  return () => source.close();
}
