import { api } from "./api";
import { store } from "./state";

export async function setVolume(volume: number, deviceIds?: string[], relative = false): Promise<void> {
  const result = await api.setVolume(volume, deviceIds, relative);
  const confirmed = new Map(result.devices.filter((d) => d.ok && d.volume !== undefined).map((d) => [d.did, d.volume!]));
  const state = store.get();
  const playback = state.playback;
  const devices = playback?.devices.map((d) => confirmed.has(d.did) ? { ...d, volume: confirmed.get(d.did)! } : d);
  const known = devices?.map((d) => d.volume).filter((v): v is number => v != null) ?? [];
  store.set({
    devices: state.devices.map((d) => confirmed.has(d.did) ? { ...d, volume: confirmed.get(d.did)! } : d),
    ...(playback && devices ? { playback: { ...playback, devices, volume: known.length ? Math.round(known.reduce((a, b) => a + b, 0) / known.length) : null, mixed_volume: new Set(known).size > 1 } } : {}),
  });
  document.dispatchEvent(new CustomEvent("micast:render-devices"));
  const failed = result.devices.filter((d) => !d.ok);
  if (failed.length) throw new Error(`${failed.length} 台音箱未能调整${confirmed.size ? "，其他音箱已更新" : ""}：${failed[0].error ?? "请检查连接"}`);
}

export async function setMute(muted: boolean, deviceIds?: string[]): Promise<void> {
  const result = await api.setMute(muted, deviceIds);
  const confirmed = new Map(result.devices.filter((d) => d.ok).map((d) => [d.did, d.muted!]));
  const state = store.get();
  const playback = state.playback;
  const devices = playback?.devices.map((d) => confirmed.has(d.did) ? { ...d, muted: confirmed.get(d.did)! } : d);
  store.set({
    devices: state.devices.map((d) => confirmed.has(d.did) ? { ...d, muted: confirmed.get(d.did)! } : d),
    ...(playback && devices ? { playback: { ...playback, devices, muted: !!devices.length && devices.every((d) => d.muted) } } : {}),
  });
  document.dispatchEvent(new CustomEvent("micast:render-devices"));
  document.dispatchEvent(new CustomEvent("micast:render-playback"));
  const failed = result.devices.filter((d) => !d.ok);
  if (failed.length) throw new Error(`${failed.length} 台音箱未能${muted ? "静音" : "恢复音量"}：${failed[0].error ?? "请检查连接"}`);
}
