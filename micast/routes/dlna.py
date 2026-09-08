"""HTTP description and SOAP control endpoints for local DLNA renderers."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from micast.dlna import (
    AV_TRANSPORT,
    CONNECTION_MANAGER,
    MEDIA_RENDERER,
    RENDERING_CONTROL,
    DlnaService,
    xml_value,
)

router = APIRouter(prefix="/dlna", tags=["dlna"])


def install(service: DlnaService) -> APIRouter:
    @router.get("/{receiver_id}/description.xml")
    async def description(receiver_id: str):
        receiver = _receiver(service, receiver_id)
        device_uuid = service.uuid_for(receiver_id)
        base = f"/dlna/{receiver_id}"
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>http://{escape(service.location_for(receiver_id).split('/')[2])}/</URLBase>
  <device>
    <deviceType>{MEDIA_RENDERER}</deviceType>
    <friendlyName>{escape(receiver.name)}</friendlyName>
    <manufacturer>MiCast</manufacturer><manufacturerURL>https://github.com/</manufacturerURL>
    <modelDescription>MiCast local audio bridge</modelDescription>
    <modelName>MiCast DLNA Renderer</modelName><modelNumber>0.1</modelNumber>
    <UDN>uuid:{device_uuid}</UDN>
    <serviceList>
      {_service_xml(AV_TRANSPORT, 'AVTransport', base)}
      {_service_xml(RENDERING_CONTROL, 'RenderingControl', base)}
      {_service_xml(CONNECTION_MANAGER, 'ConnectionManager', base)}
    </serviceList>
  </device>
</root>"""
        return _xml(xml)

    @router.get("/{receiver_id}/{service_name}.xml")
    async def service_description(receiver_id: str, service_name: str):
        _receiver(service, receiver_id)
        if service_name not in {"AVTransport", "RenderingControl", "ConnectionManager"}:
            raise HTTPException(status_code=404, detail="Unknown DLNA service")
        return _xml(_scpd(service_name))

    @router.post("/{receiver_id}/{service_name}/control")
    async def control(receiver_id: str, service_name: str, request: Request):
        _receiver(service, receiver_id)
        body = await request.body()
        soap_action = request.headers.get("soapaction", "").strip('"').rsplit("#", 1)[-1]
        try:
            values = await _dispatch(service, receiver_id, service_name, soap_action, body)
        except ValueError as exc:
            return _soap_fault(701, str(exc))
        return _soap_response(_service_type(service_name), soap_action, values)

    @router.api_route(
        "/{receiver_id}/{service_name}/event", methods=["SUBSCRIBE", "UNSUBSCRIBE"]
    )
    async def event_subscription(receiver_id: str, service_name: str, request: Request):
        _receiver(service, receiver_id)
        sid = request.headers.get("sid") or f"uuid:{service.uuid_for(receiver_id)}-{service_name}"
        return Response(status_code=200, headers={"SID": sid, "TIMEOUT": "Second-1800"})

    return router


def _receiver(service: DlnaService, receiver_id: str):
    receiver = service.receiver(receiver_id)
    if receiver is None:
        raise HTTPException(status_code=404, detail="DLNA renderer not found")
    return receiver


async def _dispatch(
    service: DlnaService, receiver_id: str, service_name: str, action: str, body: bytes
) -> dict[str, str | int]:
    state = service.state_for(receiver_id)
    if service_name == "AVTransport":
        if action == "SetAVTransportURI":
            await service.set_uri(
                receiver_id,
                xml_value(body, "CurrentURI"),
                xml_value(body, "CurrentURIMetaData"),
            )
            return {}
        if action == "Play":
            await service.play(receiver_id)
            return {}
        if action == "Pause":
            await service.pause(receiver_id)
            return {}
        if action == "Stop":
            await service.stop_playback(receiver_id)
            return {}
        if action == "Seek":
            unit = xml_value(body, "Unit")
            if unit != "REL_TIME":
                raise ValueError("Only REL_TIME seek is supported")
            await service.seek(receiver_id, _parse_rel_time(xml_value(body, "Target")))
            return {}
        if action == "GetTransportInfo":
            return {
                "CurrentTransportState": state.state,
                "CurrentTransportStatus": "OK",
                "CurrentSpeed": "1",
            }
        if action == "GetPositionInfo":
            return {
                "Track": 1,
                "TrackDuration": "00:00:00",
                "TrackMetaData": state.metadata,
                "TrackURI": state.uri,
                "RelTime": "00:00:00",
                "AbsTime": "00:00:00",
                "RelCount": 0,
                "AbsCount": 0,
            }
        if action == "GetMediaInfo":
            return {
                "NrTracks": 1,
                "MediaDuration": "00:00:00",
                "CurrentURI": state.uri,
                "CurrentURIMetaData": state.metadata,
                "NextURI": "",
                "NextURIMetaData": "",
                "PlayMedium": "NETWORK",
                "RecordMedium": "NOT_IMPLEMENTED",
                "WriteStatus": "NOT_IMPLEMENTED",
            }
    elif service_name == "RenderingControl":
        if action == "SetVolume":
            volume = max(0, min(100, int(xml_value(body, "DesiredVolume", "50"))))
            await service.set_volume(receiver_id, volume)
            return {}
        if action == "GetVolume":
            return {"CurrentVolume": await service.get_volume(receiver_id)}
        if action == "SetMute":
            muted = xml_value(body, "DesiredMute", "0") in {"1", "true"}
            await service.set_mute(receiver_id, muted)
            return {}
        if action == "GetMute":
            return {"CurrentMute": int(state.muted)}
    elif service_name == "ConnectionManager":
        if action == "GetProtocolInfo":
            sink = ",".join(
                [
                    "http-get:*:audio/mpeg:*",
                    "http-get:*:audio/mp4:*",
                    "http-get:*:audio/aac:*",
                    "http-get:*:audio/flac:*",
                    "http-get:*:audio/wav:*",
                ]
            )
            return {
                "Source": "",
                "Sink": sink,
            }
        if action == "GetCurrentConnectionIDs":
            return {"ConnectionIDs": "0"}
        if action == "GetCurrentConnectionInfo":
            return {
                "RcsID": 0,
                "AVTransportID": 0,
                "ProtocolInfo": "",
                "PeerConnectionManager": "",
                "PeerConnectionID": -1,
                "Direction": "Input",
                "Status": "OK",
            }
    raise ValueError(f"Unsupported action: {service_name}#{action}")


def _service_type(service_name: str) -> str:
    return {
        "AVTransport": AV_TRANSPORT,
        "RenderingControl": RENDERING_CONTROL,
        "ConnectionManager": CONNECTION_MANAGER,
    }[service_name]


def _parse_rel_time(target: str) -> float:
    """DLNA REL_TIME target ("HH:MM:SS" or "HH:MM:SS.mmm") → seconds."""
    parts = target.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Bad REL_TIME target: {target!r}")
    hours, minutes = int(parts[0]), int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def _service_xml(service_type: str, service_name: str, base: str) -> str:
    return f"""<service><serviceType>{service_type}</serviceType>
<serviceId>urn:upnp-org:serviceId:{service_name}</serviceId>
<SCPDURL>{base}/{service_name}.xml</SCPDURL>
<controlURL>{base}/{service_name}/control</controlURL>
<eventSubURL>{base}/{service_name}/event</eventSubURL></service>"""


def _scpd(service_name: str) -> str:
    actions = {
        "AVTransport": [
            "SetAVTransportURI",
            "Play",
            "Pause",
            "Stop",
            "Seek",
            "GetTransportInfo",
            "GetPositionInfo",
            "GetMediaInfo",
        ],
        "RenderingControl": ["SetVolume", "GetVolume", "SetMute", "GetMute"],
        "ConnectionManager": [
            "GetProtocolInfo",
            "GetCurrentConnectionIDs",
            "GetCurrentConnectionInfo",
        ],
    }[service_name]
    action_xml = "".join(f"<action><name>{name}</name></action>" for name in actions)
    return f"""<?xml version="1.0"?><scpd xmlns="urn:schemas-upnp-org:service-1-0">
<specVersion><major>1</major><minor>0</minor></specVersion>
<actionList>{action_xml}</actionList><serviceStateTable/></scpd>"""


def _soap_response(service_type: str, action: str, values: dict[str, str | int]) -> Response:
    fields = "".join(f"<{key}>{escape(str(value))}</{key}>" for key, value in values.items())
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action}Response xmlns:u="{service_type}">'
        f"{fields}</u:{action}Response></s:Body></s:Envelope>"
    )
    return _xml(body)


def _soap_fault(code: int, description: str) -> Response:
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body><s:Fault><faultcode>s:Client</faultcode>"
        "<faultstring>UPnPError</faultstring><detail>"
        '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{code}</errorCode>"
        f"<errorDescription>{escape(description)}</errorDescription>"
        "</UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    )
    return Response(body, status_code=500, media_type='text/xml; charset="utf-8"')


def _xml(body: str) -> Response:
    return Response(body, media_type='text/xml; charset="utf-8"')
