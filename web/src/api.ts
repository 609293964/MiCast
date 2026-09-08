/**
 * Minimal typed API client for MiCast.
 */

import { store } from "./state";
import { appUrl } from "./paths";

async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(appUrl(path), init);
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    if (res.status === 401 && text.includes("需要登录 MiCast")) {
      window.dispatchEvent(new Event("micast:access-required"));
    }
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json();
}

export interface AccessStatus {
  access_configured: boolean;
  setup_complete: boolean;
  auth_enabled: boolean;
  username: string;
  authenticated: boolean;
}

export interface AudioConfig {
  format: "mp3" | "flac" | "wav";
  bitrate: "128k" | "192k" | "320k";
  sample_rate: 44100 | 48000;
  auto_transcode: boolean;
}

export interface AppConfig {
  name: string;
}

export type ReceiverMode = "single" | "multi";
export type AirPlayProtocol = "auto" | "classic" | "airplay2";
export type AirPlayEngine = "local" | "airplay2";

export interface FullConfig {
  audio: AudioConfig;
  app: AppConfig;
  receiver_mode: ReceiverMode;
  airplay_protocol: AirPlayProtocol;
  airplay_engine: AirPlayEngine;
  dlna_enabled: boolean;
  sync_groups_enabled: boolean;
  large_delay_enabled: boolean;
  touchscreen_lyrics: boolean;
  default_volume: number;
  default_volume_enabled: boolean;
  sender_volume_mode: "independent" | "linked";
  notify_webhook_url: string;
  airplay2_enabled: boolean;
  airplay2_available: boolean;
  airplay2_mode: "disabled" | "single" | "multi";
  airplay2_can_add_instances: boolean;
  dlna_status: { status: string; detail: string };
  selected_device_id: string | null;
  receivers: ReceiverDefinition[];
  groups: SpeakerGroup[];
  speaker_names: Record<string, string>;
}

export interface AirPlay2Instance {
  id: string;
  name: string;
  enabled: boolean;
  status: string;
  detail: string;
  target_type: "speaker" | "group";
  target_id: string | null;
  target_name: string;
}

export interface AirPlay2State {
  enabled: boolean;
  mode: "disabled" | "single" | "multi";
  can_add_instances: boolean;
  instances: AirPlay2Instance[];
  orchestration: {
    available: boolean;
    status: "running" | "error" | "disabled" | "unavailable";
    detail: string;
  };
  summary: {
    instances_running: number;
    instances_total: number;
    mappings_healthy: number;
    mappings_total: number;
  };
  targets: Array<{ type: "speaker" | "group"; id: string; name: string }>;
}

export interface ReceiverDefinition {
  id: string;
  name: string;
  target_type: "selected" | "speaker" | "group";
  target_id: string | null;
  enabled: boolean;
}

export interface SpeakerGroup {
  id: string;
  name: string;
  speaker_ids: string[];
  // Signed offset (ms) per member, relative to `anchor_did`. Positive = later,
  // negative = earlier. The anchor's own offset is always 0 (and omitted).
  delays_ms: Record<string, number>;
  anchor_did: string | null;
  mode: "mirror" | "stereo";
  channels: Record<string, "left" | "right" | "both">;
  gains_db: Record<string, number>;
  airplay_targets?: string[];
  dlna_targets?: string[];
  network_channels?: Record<string, "left" | "right">;
}

export type NetworkDeviceKind = "speaker" | "tv" | "projector";

export interface NetworkDevice {
  volume_control?: boolean;
  volume_readback?: boolean;
  id: string;
  name: string;
  host?: string;
  port?: number;
  model: string;
  kind: NetworkDeviceKind;
  online: boolean;
  supported: boolean;
  unsupported_reason: string;
  attached_group: string | null;
  stream_status: string;
  stream_detail: string;
}

export interface Status {
  status: string;
  pcm_source: string;
  audio: AudioConfig;
  stream_url: string;
  error_count: number;
  receivers: ReceiverInfo[];
  airplay_protocol: AirPlayProtocol;
  airplay_engine: AirPlayEngine;
  orchestration: {
    configured: boolean;
    status: string;
    detail: string;
  };
  diagnostics: {
    raop: Record<string, { active_sessions: number; total_sessions: number; decode_errors: number; dropped_packets: number; resend_requests: number; timing_requests: number; timing_responses: number; input_buffer_ms: number }>;
    streams: Record<string, { clients: number; bytes_sent: number; dropped_chunks: number; latency: LatencyMetrics }>;
    sinks: Record<string, Record<string, SinkLatencyMetrics>>;
  };
}

export interface XiaomiStatus {
  logged_in: boolean;
  user_id: string | null;
  status?: "connected" | "expired" | "disconnected" | "never_connected";
  ever_logged_in?: boolean;
}

export interface SpeakerEq {
  enabled: boolean;
  bands: number[];
  preset: string;
}

export interface Device {
  did: string;
  name: string;
  alias: string;
  model: string;
  presence?: string;
  play_error?: string | null;
  playing?: boolean;
  muted?: boolean;
  enabled: boolean;
  selected: boolean;
  volume?: number | null;
  eq?: SpeakerEq;
}

export interface ReceiverInfo {
  did: string;
  name: string;
  status: "idle" | "running" | "error";
  stream_url: string;
  detail?: string;
}

export interface PlaybackState {
  playing: boolean;
  paused: boolean;
  volume: number | null;
  mixed_volume: boolean;
  muted: boolean;
  devices: Array<{
    did: string;
    name: string;
    volume: number | null;
    playing: boolean;
    paused: boolean;
    muted: boolean;
    state: "playing" | "paused" | "idle";
  }>;
}

export interface DebugState {
  logged_in: boolean;
  selected_device_id: string | null;
  devices: Array<{ did: string; name: string; hardware: string; presence: string; miotDID: string }>;
  pcm_source: string;
  stream_url: string;
  audio_config: AudioConfig;
  bridge_status: { status: string; error_count: number };
  stream_clients: number;
  stream_bytes_sent: number;
  diagnostics: {
    raop: Record<string, { active_sessions: number; total_sessions: number; decode_errors: number; dropped_packets: number; resend_requests: number; timing_requests: number; timing_responses: number; input_buffer_ms: number }>;
    streams: Record<string, { clients: number; bytes_sent: number; dropped_chunks: number; latency: LatencyMetrics }>;
  };
  logs: Array<{ time: string; level: string; logger: string; message: string }>;
}

export interface TestMedia {
  token: string;
  name: string;
  size: number;
  duration: number | null;
  codec: string;
  media_type: string;
  converted: boolean;
}

export interface LatencyMetrics {
  encoding_ms: number;
  stream_buffer_ms: number;
  send_queue_ms: number;
  estimated_ms: number;
}

export interface SinkLatencyMetrics {
  manual_ms: number;
  startup_ms: number;
  effective_ms: number;
  buffer_ms: number;
}

export interface TopologyNode {
  id: string;
  kind: "source" | "engine" | "pipeline" | "stream" | "cloud" | "speaker";
  label: string;
  protocol?: string;
  status?: string;
  active?: boolean;
  enabled?: boolean;
  [key: string]: unknown;
}

export interface TopologyEdge {
  from: string;
  to: string;
  protocol?: string;
  direction?: "push" | "pull" | "control";
  latency_ms?: number;
  segments?: Record<string, number>;
  estimated?: boolean;
  active?: boolean;
  stalled?: boolean;
  compensation_ms?: number;
  audio_delay_ms?: number;
  [key: string]: unknown;
}

export interface Topology {
  ts: number;
  status: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export const api = {
  getAccessStatus(): Promise<AccessStatus> {
    return apiFetch("/api/access/status");
  },

  setupAccess(payload: { auth_enabled: boolean; username: string; password: string; password_confirm: string }): Promise<{ ok: boolean }> {
    return apiFetch("/api/access/setup", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  },

  completeSetup(): Promise<{ ok: boolean }> {
    return apiFetch("/api/access/setup/complete", { method: "POST" });
  },

  loginAccess(username: string, password: string): Promise<{ ok: boolean }> {
    return apiFetch("/api/access/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
    });
  },

  logoutAccess(): Promise<{ ok: boolean }> {
    return apiFetch("/api/access/logout", { method: "POST" });
  },

  updateAccess(payload: { auth_enabled: boolean; username: string; password: string; password_confirm: string }): Promise<{ ok: boolean }> {
    return apiFetch("/api/access/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  },
  getStatus(): Promise<Status> {
    return apiFetch("/api/status");
  },

  getAudioConfig(): Promise<AudioConfig> {
    return apiFetch("/api/config/audio");
  },

  setAudioConfig(config: Partial<AudioConfig>): Promise<AudioConfig> {
    return apiFetch("/api/config/audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
  },

  getConfig(): Promise<FullConfig> {
    return apiFetch("/api/config");
  },

  setAppName(name: string): Promise<AppConfig> {
    return apiFetch("/api/config/app-name", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  },

  setReceiverMode(mode: ReceiverMode): Promise<{ receiver_mode: ReceiverMode }> {
    return apiFetch("/api/config/receiver-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  },

  setAirPlayProtocol(protocol: AirPlayProtocol): Promise<{ airplay_protocol: AirPlayProtocol }> {
    return apiFetch("/api/config/airplay-protocol", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ protocol }),
    });
  },

  setDlnaEnabled(enabled: boolean): Promise<{ dlna_enabled: boolean }> {
    return apiFetch("/api/config/dlna", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  },

  setSyncGroupsEnabled(enabled: boolean): Promise<{ sync_groups_enabled: boolean }> {
    return apiFetch("/api/config/sync-groups", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  },

  setLargeDelayEnabled(enabled: boolean): Promise<{ large_delay_enabled: boolean }> {
    return apiFetch("/api/config/large-delay", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  },

  setTouchscreenLyrics(enabled: boolean): Promise<{ touchscreen_lyrics: boolean }> {
    return apiFetch("/api/config/touchscreen-lyrics", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  },

  setDefaultVolume(volume: number, enabled: boolean): Promise<{ default_volume: number; default_volume_enabled: boolean }> {
    return apiFetch("/api/config/default-volume", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume, enabled }),
    });
  },

  setSenderVolumeMode(mode: "independent" | "linked"): Promise<{ sender_volume_mode: "independent" | "linked" }> {
    return apiFetch("/api/config/sender-volume", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }),
    });
  },

  setNotifyWebhook(url: string): Promise<{ notify_webhook_url: string }> {
    return apiFetch("/api/config/notify-webhook", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  },

  setAirPlay2Enabled(enabled: boolean): Promise<{ airplay2_enabled: boolean }> {
    return apiFetch("/api/config/airplay2", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  },

  getAirPlay2State(): Promise<AirPlay2State> {
    return apiFetch("/api/airplay2");
  },

  saveAirPlay2Instance(instance: { id?: string; name: string; target_type: "speaker" | "group"; target_id: string; enabled?: boolean }): Promise<AirPlay2Instance> {
    return apiFetch("/api/airplay2/instances", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(instance),
    });
  },

  setAirPlay2InstanceEnabled(id: string, enabled: boolean): Promise<AirPlay2Instance> {
    return apiFetch(`/api/airplay2/instances/${encodeURIComponent(id)}/enabled`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }),
    });
  },

  deleteAirPlay2Instance(id: string): Promise<{ ok: boolean; warning?: string | null }> {
    return apiFetch(`/api/airplay2/instances/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  getXiaomiStatus(): Promise<XiaomiStatus> {
    return apiFetch("/api/xiaomi/status");
  },

  logoutXiaomi(): Promise<{ ok: boolean }> {
    return apiFetch("/api/xiaomi/logout", { method: "POST" });
  },

  startQRLogin(): Promise<{ qr_url: string; scan_token: string }> {
    return apiFetch("/api/xiaomi/login/qr/start", { method: "POST" });
  },

  pollQRLogin(scanToken: string): Promise<{ status: string; user_id?: string; pass_token?: string }> {
    return apiFetch(`/api/xiaomi/login/qr/poll?scan_token=${encodeURIComponent(scanToken)}`);
  },

  loginWithCookie(userId: string, passToken: string): Promise<{ success: boolean }> {
    return apiFetch("/api/xiaomi/login/cookie", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, pass_token: passToken }),
    });
  },

  getDevices(): Promise<Device[]> {
    return apiFetch("/api/devices");
  },

  getEqPresets(): Promise<{ bands_hz: number[]; presets: Record<string, number[]> }> {
    return apiFetch("/api/devices/eq/presets");
  },

  setDeviceEq(did: string, eq: SpeakerEq): Promise<{ did: string; eq: SpeakerEq }> {
    return apiFetch("/api/devices/eq", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ did, ...eq }),
    });
  },

  selectDevice(did: string): Promise<{ selected: string }> {
    return apiFetch("/api/devices/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ did }),
    });
  },

  setAlias(did: string, alias: string): Promise<{ did: string; alias: string }> {
    return apiFetch("/api/devices/alias", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ did, alias }),
    });
  },

  setEnabled(did: string, enabled: boolean): Promise<{ did: string; enabled: boolean }> {
    return apiFetch("/api/devices/enabled", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ did, enabled }),
    });
  },

  getReceivers(): Promise<ReceiverInfo[]> {
    return apiFetch("/api/receivers");
  },

  createReceiver(payload: Pick<ReceiverDefinition, "name" | "target_type" | "target_id">): Promise<ReceiverDefinition> {
    return apiFetch("/api/receivers/definitions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  },

  updateReceiver(id: string, payload: Partial<ReceiverDefinition>): Promise<ReceiverDefinition> {
    return apiFetch(`/api/receivers/definitions/${encodeURIComponent(id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  },

  deleteReceiver(id: string): Promise<{ ok: boolean }> {
    return apiFetch(`/api/receivers/definitions/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  getAirPlayDevices(): Promise<NetworkDevice[]> {
    return apiFetch("/api/airplay-devices");
  },

  getDlnaDevices(): Promise<NetworkDevice[]> {
    return apiFetch("/api/dlna-devices");
  },

  createGroup(
    name: string,
    speakerIds: string[],
    airplayTargets: string[] = [],
    dlnaTargets: string[] = []
  ): Promise<SpeakerGroup> {
    return apiFetch("/api/receivers/groups", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        speaker_ids: speakerIds,
        airplay_targets: airplayTargets,
        dlna_targets: dlnaTargets,
      }),
    });
  },

  updateGroup(id: string, payload: Partial<SpeakerGroup>): Promise<SpeakerGroup> {
    return apiFetch(`/api/receivers/groups/${encodeURIComponent(id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  },

  calibrateGroupDelay(id: string): Promise<{ ok: boolean; group: SpeakerGroup; measured_spread_ms: number; request_offsets_ms: Record<string, number> }> {
    return apiFetch(`/api/debug/groups/${encodeURIComponent(id)}/calibrate-delay`, {
      method: "POST",
    });
  },

  startGroupCalibration(id: string, mediaToken?: string): Promise<{ ok: boolean; group: SpeakerGroup }> {
    return apiFetch(`/api/debug/groups/${encodeURIComponent(id)}/calibration/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mediaToken ? { media_token: mediaToken } : {}),
    });
  },

  stopGroupCalibration(id: string): Promise<{ ok: boolean }> {
    return apiFetch(`/api/debug/groups/${encodeURIComponent(id)}/calibration/stop`, { method: "POST" });
  },

  deleteGroup(id: string): Promise<{ ok: boolean }> {
    return apiFetch(`/api/receivers/groups/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  play(): Promise<{ ok: boolean; url: string }> {
    return apiFetch("/api/playback/play", { method: "POST" });
  },

  pause(): Promise<{ ok: boolean }> {
    return apiFetch("/api/playback/pause", { method: "POST" });
  },

  stopPlayback(): Promise<{ ok: boolean; stopped: string[]; disconnected: number }> {
    return apiFetch("/api/playback/stop", { method: "POST" });
  },

  kickStream(receiverId: string): Promise<{ ok: boolean; kicked: number; sender_sessions: number; stopped: string[] }> {
    return apiFetch(`/api/debug/stream/${encodeURIComponent(receiverId)}/kick`, { method: "POST" });
  },

  getPlaybackState(refresh = false): Promise<PlaybackState> {
    return apiFetch(`/api/playback/state${refresh ? "?refresh=true" : ""}`);
  },

  async setVolume(volume: number, deviceIds?: string[], relative = false): Promise<void> {
    const result: { devices: Array<{ did: string; ok: boolean; volume?: number; error?: string }> } = await apiFetch("/api/playback/volume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [relative ? "delta" : "volume"]: volume, device_ids: deviceIds }),
    });
    const confirmed = new Map(result.devices.filter(d => d.ok && d.volume !== undefined).map(d => [d.did, d.volume!]));
    const state = store.get();
    const playback = state.playback;
    const devices = playback?.devices.map(d => confirmed.has(d.did) ? { ...d, volume: confirmed.get(d.did)! } : d);
    const known = devices?.map(d => d.volume).filter((v): v is number => v != null) ?? [];
    store.set({
      devices: state.devices.map(d => confirmed.has(d.did) ? { ...d, volume: confirmed.get(d.did)! } : d),
      ...(playback && devices ? { playback: { ...playback, devices, volume: known.length ? Math.round(known.reduce((a, b) => a + b, 0) / known.length) : null, mixed_volume: new Set(known).size > 1 } } : {}),
    });
    document.dispatchEvent(new CustomEvent("micast:render-devices"));
    const failed = result.devices.filter(d => !d.ok);
    if (failed.length) throw new Error(`${failed.length} 台音箱未能调整${confirmed.size ? "，其他音箱已更新" : ""}：${failed[0].error ?? "请检查连接"}`);
  },

  async setMute(muted: boolean, deviceIds?: string[]): Promise<void> {
    const result: { muted: boolean; devices: Array<{ did: string; ok: boolean; muted?: boolean; error?: string }> } = await apiFetch("/api/playback/mute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ muted, device_ids: deviceIds }),
    });
    const confirmed = new Map(result.devices.filter(d => d.ok).map(d => [d.did, d.muted!]));
    const state = store.get();
    const playback = state.playback;
    const devices = playback?.devices.map(d => confirmed.has(d.did) ? { ...d, muted: confirmed.get(d.did)! } : d);
    store.set({
      devices: state.devices.map(d => confirmed.has(d.did) ? { ...d, muted: confirmed.get(d.did)! } : d),
      ...(playback && devices ? { playback: { ...playback, devices, muted: !!devices.length && devices.every(d => d.muted) } } : {}),
    });
    document.dispatchEvent(new CustomEvent("micast:render-devices"));
    document.dispatchEvent(new CustomEvent("micast:render-playback"));
    const failed = result.devices.filter(d => !d.ok);
    if (failed.length) throw new Error(`${failed.length} 台音箱未能${muted ? "静音" : "恢复音量"}：${failed[0].error ?? "请检查连接"}`);
  },

  async getDeviceVolume(did: string): Promise<number> {
    const result: { devices: Array<{ ok: boolean; volume?: number; error?: string }> } = await apiFetch("/api/playback/volume/levels", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_ids: [did] }),
    });
    const item = result.devices[0];
    if (!item?.ok || item.volume === undefined) throw new Error(item?.error ?? "无法读取音量");
    return item.volume;
  },

  getDebugState(): Promise<DebugState> {
    return apiFetch("/api/debug/state");
  },

  getTopology(): Promise<Topology> {
    return apiFetch("/api/topology");
  },

  debugTTS(text: string, deviceId?: string): Promise<{ ok: boolean; result: unknown }> {
    return apiFetch("/api/debug/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, device_id: deviceId }),
    });
  },

  debugPlayUrl(url: string, method = "music_url"): Promise<{ ok: boolean; result: unknown }> {
    return apiFetch("/api/debug/play_url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, method }),
    });
  },

  uploadTestMedia(file: File): Promise<TestMedia> {
    const body = new FormData();
    body.append("file", file);
    return apiFetch("/api/debug/media", { method: "POST", body });
  },

  deleteTestMedia(token: string): Promise<{ ok: boolean }> {
    return apiFetch(`/api/debug/media/${encodeURIComponent(token)}`, { method: "DELETE" });
  },

  startDebugTest(payload: { device_ids: string[]; source: "builtin" | "upload" | "url"; media_token?: string; url?: string }): Promise<{ ok: boolean; session_id: string; members: string[]; source: string }> {
    return apiFetch("/api/debug/test/start", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  },

  stopDebugTest(sessionId: string, restore = true): Promise<{ ok: boolean; restored: number }> {
    return apiFetch("/api/debug/test/stop", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId, restore }),
    });
  },

  debugAction(action: "pause" | "play" | "stop" | "volume", payload?: { volume?: number }): Promise<{ ok: boolean; result: { name?: string; [key: string]: unknown } }> {
    return apiFetch("/api/debug/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...payload }),
    });
  },
};
