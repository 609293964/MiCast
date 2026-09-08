"""AirPlay receiver status routes."""

from fastapi import APIRouter, HTTPException

from micast.audio_bridge import AudioBridge
from micast.config import settings
from micast.dlna import DlnaService


def install(
    bridge: AudioBridge,
    dlna: DlnaService | None = None,
) -> APIRouter:
    # The router is created per install() (not module-level) so tests mounting
    # a second instance never share accumulated handlers with the real app.
    router = APIRouter(prefix="/api/receivers", tags=["receivers"])
    @router.get("")
    async def get_receivers():
        return bridge.status.get("receivers", [])

    @router.get("/definitions")
    async def get_receiver_definitions():
        return [item.model_dump() for item in settings.receivers]

    @router.post("/definitions")
    async def create_receiver(payload: dict):
        name = str(payload.get("name", "")).strip()
        target_type = payload.get("target_type", "selected")
        target_id = payload.get("target_id")
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        if target_type not in ("selected", "speaker", "group"):
            raise HTTPException(status_code=400, detail="invalid target_type")
        receiver = settings.add_receiver(name, target_type, target_id)
        await bridge.apply_config_change()
        if dlna:
            await dlna.reconcile()
        return receiver.model_dump()

    @router.delete("/definitions/{receiver_id}")
    async def delete_receiver(receiver_id: str):
        if not settings.remove_receiver(receiver_id):
            raise HTTPException(status_code=404, detail="receiver not found")
        await bridge.apply_config_change()
        if dlna:
            await dlna.reconcile()
        return {"ok": True}

    @router.patch("/definitions/{receiver_id}")
    async def update_receiver(receiver_id: str, payload: dict):
        # Classic AirPlay entries are not re-mappable: the target is fixed at
        # creation (a speaker/group pick, or "follow the selected speaker").
        # Remapping is an AirPlay 2 concept and lives on its instances.
        if "target_type" in payload or "target_id" in payload:
            raise HTTPException(status_code=400, detail="经典 AirPlay 入口不支持修改播放目标")
        receiver = settings.update_receiver(
            receiver_id,
            name=payload.get("name"),
            enabled=payload.get("enabled"),
        )
        if receiver is None:
            raise HTTPException(status_code=404, detail="receiver not found")
        await bridge.apply_config_change()
        if dlna:
            await dlna.reconcile()
        return receiver.model_dump()

    @router.post("/groups")
    async def create_group(payload: dict):
        name = str(payload.get("name", "")).strip()
        speaker_ids = payload.get("speaker_ids", [])
        airplay_targets = payload.get("airplay_targets") or []
        dlna_targets = payload.get("dlna_targets") or []
        member_count = (
            (len(speaker_ids) if isinstance(speaker_ids, list) else 0)
            + len(airplay_targets)
            + len(dlna_targets)
        )
        if not name or not isinstance(speaker_ids, list) or member_count < 2:
            raise HTTPException(
                status_code=400, detail="组合至少需要两个成员（音箱或网络设备）"
            )
        try:
            group = settings.add_group(
                name,
                [str(item) for item in speaker_ids],
                airplay_targets=airplay_targets,
                dlna_targets=dlna_targets,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings.add_receiver(name, "group", group.id)
        await bridge.apply_config_change()
        if dlna:
            await dlna.reconcile()
        return group.model_dump()

    @router.patch("/groups/{group_id}")
    async def update_group(group_id: str, payload: dict):
        try:
            group = settings.update_group(
                group_id,
                name=payload.get("name"),
                speaker_ids=payload.get("speaker_ids"),
                delays_ms=payload.get("delays_ms"),
                mode=payload.get("mode"),
                channels=payload.get("channels"),
                gains_db=payload.get("gains_db"),
                airplay_targets=payload.get("airplay_targets"),
                dlna_targets=payload.get("dlna_targets"),
                network_channels=payload.get("network_channels"),
                anchor_did=payload.get("anchor_did"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")
        # One funnel for every field: the plan diff decides what to rebuild,
        # live-apply (delays), or replay (membership) — no per-key dispatch here.
        await bridge.apply_config_change()
        if dlna:
            await dlna.reconcile()
        return group.model_dump()

    @router.delete("/groups/{group_id}")
    async def delete_group(group_id: str):
        # Uniform pre-delete reference check: classic receivers AND AirPlay 2
        # instances. AirPlay 2 references refuse the delete — the instance
        # would dangle, silently collapsing its stream plan.
        refs = settings.target_references("group", group_id)
        airplay2_refs = [ref for ref in refs if ref["kind"] == "airplay2"]
        if airplay2_refs:
            names = "、".join(f"「{ref['name']}」" for ref in airplay2_refs)
            raise HTTPException(
                status_code=409,
                detail=f"组合正被 AirPlay 2 入口 {names} 使用，请先删除或修改这些入口",
            )
        for ref in refs:
            settings.remove_receiver(ref["id"])
        if not settings.remove_group(group_id):
            raise HTTPException(status_code=404, detail="分组不存在")
        await bridge.apply_config_change()
        if dlna:
            await dlna.reconcile()
        return {"ok": True}

    return router
