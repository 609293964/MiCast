import type { TestMedia } from "./api";

let current: TestMedia | null = null;

export function getTestMedia(): TestMedia | null {
  return current;
}

export function setTestMedia(media: TestMedia | null): void {
  current = media;
}
