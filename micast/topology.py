"""Build a node/edge snapshot of the live audio paths for the topology view.

Pure assembly over existing state: AudioBridge diagnostics, StreamServer
client sets, settings receiver/group config and DeviceManager playback state.
No new measurements are taken here, so it is cheap enough to rebuild once per
second for the SSE stream.

Node kinds: source | engine | pipeline | stream | cloud | speaker
Edge directions: "push" (source -> MiCast), "pull" (speaker fetches the HTTP
stream), "control" (dashed; MiCast -> Mi Cloud -> speaker command path).

Naming rules: the receiver (entry) name lives only on the engine node — it is
the name phones see in the AirPlay picker. Sources show the sender device,
streams show just the channel, and the pipeline (转码器) only exists when
audio is actually transcoded (raw passthrough links engine→stream directly).

Latency values are server-side estimates (encoding / stream buffer / send
queue / RAOP input buffer). They do not include the speaker's own playback
buffer, so edges carrying them are marked ``estimated: true``.
"""

from __future__ import annotations

import re
import time

from micast.config import settings

# One cloud node per provider: today only Xiaomi exists, but a future 小度
# integration becomes its own floating cloud without any frontend change.
CLOUD_ID_XIAOMI = "cloud:xiaomi"

# Channel/EQ suffixes produced by split pipelines (audio_bridge variants):
# base stream, stereo channels (-L/-R) and per-EQ splits (-q1, -Lq1, …).
_STREAM_ID_RE = re.compile(r"^(?P<rid>.*?)(?P<chan>-[LR])?(?P<eq>-q\d+)?$")


def _split_stream_id(stream_id: str) -> tuple[str, str | None]:
    """Map a stream id back to (receiver_id, channel)."""
    match = _STREAM_ID_RE.match(stream_id)
    if not match:
        return stream_id, None
    channel = match.group("chan")
    return match.group("rid"), {"-L": "left", "-R": "right"}.get(channel or "")


def build_topology(bridge, device_manager) -> dict:
    diagnostics = bridge.diagnostics
    raop = diagnostics.get("raop", {})
    streams = diagnostics.get("streams", {})
    airplay_targets = diagnostics.get("airplay_targets", {})
    dlna_targets = diagnostics.get("dlna_targets", {})
    receiver_status = {item["did"]: item for item in bridge.status.get("receivers", [])}

    nodes: list[dict] = []
    edges: list[dict] = []
    speaker_ids: list[str] = []

    receivers = [
        (receiver, "classic", None)
        for receiver in settings.active_receivers()
    ]
    airplay2_status = {
        item.get("id"): item for item in bridge.status.get("airplay2_instances", [])
    }
    receivers.extend(
        (instance, "airplay2", airplay2_status.get(instance.id, {}))
        for instance in settings.airplay2_instances
        if instance.enabled
    )
    for receiver, ingress, ingress_status in receivers:
        rid = receiver.id
        info = receiver_status.get(rid, {})
        raop_info = raop.get(rid, {})
        targets = settings.receiver_targets(rid)
        stream_active = any(
            stream_id == rid or stream_id.startswith(f"{rid}-")
            for stream_id, stream_info in streams.items()
            if stream_info.get("flowing")
        )
        target_active = any(
            device_manager.owner_of(did) == rid and device_manager.is_playing(did)
            for did in targets
        )
        sessions = (
            int(stream_active or target_active)
            if ingress == "airplay2"
            else raop_info.get("active_sessions", 0)
        )

        source_node = {
            "id": f"src:{rid}",
            "kind": "source",
            "label": receiver.name,
            "protocol": "AirPlay 2" if ingress == "airplay2" else "经典 AirPlay",
            "active": sessions > 0,
            "sessions": sessions,
        }
        clients = [c for c in raop_info.get("clients", []) if c.get("host")]
        if clients:
            first = clients[0]
            source_node["device"] = first.get("name") or first["host"]
            if len(clients) > 1:
                source_node["device"] += f" 等 {len(clients)} 台"
        nodes.append(source_node)
        nodes.append(
            {
                "id": f"engine:{rid}",
                "kind": "engine",
                "label": receiver.name,
                "status": (ingress_status or info).get("status", "idle"),
                "detail": (ingress_status or info).get("detail", ""),
                "dropped_packets": raop_info.get("dropped_packets", 0),
                "decode_errors": raop_info.get("decode_errors", 0),
                "resend_requests": raop_info.get("resend_requests", 0),
            }
        )
        input_buffer_ms = raop_info.get("input_buffer_ms") or 0
        edges.append(
            {
                "from": f"src:{rid}",
                "to": f"engine:{rid}",
                "protocol": "AirPlay 2" if ingress == "airplay2" else "RAOP",
                "direction": "push",
                "latency_ms": input_buffer_ms,
                "segments": {"input_buffer_ms": input_buffer_ms},
                "estimated": True,
                "active": sessions > 0,
            }
        )

        speaker_ids.extend(did for did in targets if did not in speaker_ids)
        delays = settings.receiver_target_delays(rid)
        group = settings.group_for_receiver(rid)
        stereo = group is not None and group.mode == "stereo"

        # The transcoder is a real node: PCM flows in, the configured codec
        # flows out (stereo fans out to two channel streams). When audio is
        # passed through raw there is no transcoder — the engine connects to
        # the stream directly.
        transcoding = settings.audio.auto_transcode or stereo
        if transcoding:
            nodes.append(
                {
                    "id": f"pipe:{rid}",
                    "kind": "pipeline",
                    "label": "转码器",
                    "input": "PCM",
                    "output": settings.audio.format.upper(),
                }
            )
            edges.append(
                {
                    "from": f"engine:{rid}",
                    "to": f"pipe:{rid}",
                    "protocol": "PCM",
                    "direction": "push",
                    "active": sessions > 0,
                }
            )

        # One stream node per (channel, EQ signature) variant; each speaker
        # pulls the stream matching its own channel and EQ.
        for variant in settings.receiver_stream_variants(rid):
            if not stereo and variant["base"]:
                continue  # stray channel config on a non-stereo receiver
            stream_id = f"{rid}{variant['suffix']}"
            stream_info = streams.get(stream_id, {})
            nodes.append(
                _stream_node(
                    stream_id, receiver.name, variant["channel"], stream_info,
                    eq=bool(variant["eq"]),
                )
            )
            edges.append(_encode_edge(rid, stream_id, stream_info, sessions, transcoding))
            for did in targets:
                if settings.stream_suffix(rid, did) != variant["suffix"]:
                    continue
                edges.append(
                    _pull_edge(stream_id, did, stream_info, delays, group, receiver.name)
                )

        # External AirPlay devices attached to the group: MiCast pushes RAOP
        # to them straight from the engine (they pull nothing over HTTP).
        for target_id, runtime in airplay_targets.get(rid, {}).items():
            streaming = runtime.get("status") == "streaming"
            nodes.append(
                {
                    "id": f"apt:{target_id}",
                    "kind": "speaker",
                    "label": runtime.get("name") or target_id,
                    "status": (
                        "playing" if streaming
                        else "error" if runtime.get("status") == "error" else "idle"
                    ),
                    "enabled": True,
                    "airplay_target": True,
                    "detail": runtime.get("detail", ""),
                }
            )
            edges.append(
                {
                    "from": f"engine:{rid}",
                    "to": f"apt:{target_id}",
                    "protocol": "RAOP 推送",
                    "direction": "push",
                    "active": streaming,
                }
            )

        # External DLNA renderers: told via SOAP to pull the stream URL.
        for target_id, runtime in dlna_targets.get(rid, {}).items():
            playing = runtime.get("status") == "playing"
            nodes.append(
                {
                    "id": f"dlt:{target_id}",
                    "kind": "speaker",
                    "label": runtime.get("name") or target_id,
                    "status": (
                        "playing" if playing
                        else "error" if runtime.get("status") == "error" else "idle"
                    ),
                    "enabled": True,
                    "dlna_target": True,
                    "detail": runtime.get("detail", ""),
                }
            )
            edges.append(
                {
                    "from": f"engine:{rid}",
                    "to": f"dlt:{target_id}",
                    "protocol": "DLNA 投放",
                    "direction": "push",
                    "active": playing,
                }
            )

    # DLNA entrances: the speaker fetches the controller's URI directly, so
    # DLNA appears as a control-only source driving speakers via the cloud.
    if settings.dlna_enabled:
        for receiver in settings.active_receivers():
            node_id = f"dlna:{receiver.id}"
            nodes.append(
                {
                    "id": node_id,
                    "kind": "source",
                    "label": f"DLNA · {receiver.name}",
                    "protocol": "DLNA",
                    "active": False,
                }
            )
            for did in settings.receiver_targets(receiver.id):
                owner = device_manager.owner_of(did)
                active = owner == f"dlna:{receiver.id}"
                if active:
                    for node in nodes:
                        if node["id"] == node_id:
                            node["active"] = True
                edges.append(
                    {
                        "from": node_id,
                        "to": CLOUD_ID_XIAOMI,
                        "protocol": "DLNA",
                        "direction": "control",
                        "active": active,
                        "via": receiver.id,
                    }
                )
                if did not in speaker_ids:
                    speaker_ids.append(did)

    if speaker_ids:
        nodes.append(
            {
                "id": CLOUD_ID_XIAOMI,
                "kind": "cloud",
                "label": "米家",
                "provider": "米家",
            }
        )
    for did in speaker_ids:
        status = (
            "playing"
            if device_manager.is_playing(did)
            else "paused"
            if device_manager.is_paused(did)
            else "idle"
        )
        nodes.append(
            {
                "id": f"spk:{did}",
                "kind": "speaker",
                "label": device_manager.get_alias(did),
                "status": status,
                "enabled": device_manager.is_enabled(did),
            }
        )
        edges.append(
            {
                "from": CLOUD_ID_XIAOMI,
                "to": f"spk:{did}",
                "direction": "control",
                "active": status == "playing",
            }
        )

    return {
        "ts": time.time(),
        "status": bridge.status.get("status", "idle"),
        "nodes": nodes,
        "edges": edges,
    }


def _stream_node(
    stream_id: str, receiver_name: str, channel: str | None, info: dict, eq: bool = False
) -> dict:
    # Streams are nameless: the channel (or bare "音频流" on the frontend) is
    # the whole label. The raw path stays in the detail card.
    return {
        "id": f"stream:{stream_id}",
        "kind": "stream",
        "label": f"/stream/{stream_id}",
        "receiver": receiver_name,
        "channel": channel,
        "eq": eq,
        "clients": info.get("clients", 0),
        "bytes_sent": info.get("bytes_sent", 0),
        "dropped_chunks": info.get("dropped_chunks", 0),
    }


def _encode_edge(
    receiver_id: str, stream_id: str, info: dict, sessions: int, transcoding: bool
) -> dict:
    """Transcoder→stream (or, in raw passthrough, engine→stream) edge."""
    latency = info.get("latency") or {}
    encoding_ms = latency.get("encoding_ms") or 0
    return {
        "from": f"pipe:{receiver_id}" if transcoding else f"engine:{receiver_id}",
        "to": f"stream:{stream_id}",
        "protocol": settings.audio.format.upper() if transcoding else "PCM 直出",
        "direction": "push",
        "latency_ms": encoding_ms,
        "segments": {"encoding_ms": encoding_ms},
        "estimated": True,
        "active": sessions > 0,
    }


def _pull_edge(
    stream_id: str, did: str, info: dict, delays: dict, group, receiver_name: str
) -> dict:
    """Speaker -> stream fetch edge. Active means bytes are actually moving:
    a paused speaker can hold the HTTP connection open with zero traffic —
    that state is reported as ``stalled`` instead of looking alive."""
    latency = info.get("latency") or {}
    buffer_ms = latency.get("stream_buffer_ms") or 0
    queue_ms = latency.get("send_queue_ms") or 0
    flowing = bool(info.get("flowing"))
    clients = info.get("clients", 0)
    edge = {
        "from": f"stream:{stream_id}",
        "to": f"spk:{did}",
        "protocol": "HTTP",
        "direction": "pull",
        "latency_ms": buffer_ms + queue_ms,
        "segments": {"stream_buffer_ms": buffer_ms, "send_queue_ms": queue_ms},
        "estimated": True,
        "active": flowing,
        "stalled": clients > 0 and not flowing,
        "receiver": receiver_name,
    }
    if delays.get(did):
        edge["compensation_ms"] = delays[did]
    if group is not None and group.delays_ms.get(did):
        edge["audio_delay_ms"] = group.delays_ms[did]
    return edge
