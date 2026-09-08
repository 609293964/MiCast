/** Resolve every UI endpoint from the one canonical application base URL. */
export function appUrl(path = ""): string {
  return new URL(path.replace(/^\/+/, ""), document.baseURI).toString();
}

export function appWebSocketUrl(path: string): string {
  const url = new URL(path.replace(/^\/+/, ""), document.baseURI);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

