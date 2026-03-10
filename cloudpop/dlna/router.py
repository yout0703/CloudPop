"""DLNA description and SOAP control routes."""

from __future__ import annotations

from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response


router = APIRouter(tags=["dlna"])

_CONTENT_DIRECTORY_TYPE = "urn:schemas-upnp-org:service:ContentDirectory:1"
_CONNECTION_MANAGER_TYPE = "urn:schemas-upnp-org:service:ConnectionManager:1"


@router.get("/dlna/device.xml", include_in_schema=False)
async def device_description(request: Request) -> Response:
    info = request.app.state.dlna_device_info
    body = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <URLBase>{escape(_base_url(request))}</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{escape(info.friendly_name)}</friendlyName>
    <manufacturer>CloudPop</manufacturer>
    <manufacturerURL>https://github.com/linglin/CloudPop</manufacturerURL>
    <modelDescription>CloudPop DLNA Media Server</modelDescription>
    <modelName>CloudPop DLNA</modelName>
    <modelNumber>0.1</modelNumber>
    <serialNumber>{escape(info.uuid)}</serialNumber>
    <UDN>uuid:{escape(info.uuid)}</UDN>
    <dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>
    <presentationURL>/</presentationURL>
    <iconList>
      <icon>
        <mimetype>image/png</mimetype>
        <width>120</width>
        <height>120</height>
        <depth>24</depth>
        <url>/dlna/icon.png</url>
      </icon>
    </iconList>
    <serviceList>
      <service>
        <serviceType>{_CONTENT_DIRECTORY_TYPE}</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <SCPDURL>/dlna/content_directory.xml</SCPDURL>
        <controlURL>/dlna/control/content_directory</controlURL>
        <eventSubURL>/dlna/event/content_directory</eventSubURL>
      </service>
      <service>
        <serviceType>{_CONNECTION_MANAGER_TYPE}</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/dlna/connection_manager.xml</SCPDURL>
        <controlURL>/dlna/control/connection_manager</controlURL>
        <eventSubURL>/dlna/event/connection_manager</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>
"""
    return Response(content=body, media_type="text/xml; charset=utf-8")


@router.get("/dlna/icon.png", include_in_schema=False)
async def dlna_icon() -> Response:
    # 1x1 transparent png
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc`\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return Response(content=png, media_type="image/png")


@router.get("/dlna/content_directory.xml", include_in_schema=False)
async def content_directory_scpd() -> Response:
    return Response(
        content=_content_directory_scpd(),
        media_type="text/xml; charset=utf-8",
    )


@router.get("/dlna/connection_manager.xml", include_in_schema=False)
async def connection_manager_scpd() -> Response:
    return Response(
        content=_connection_manager_scpd(),
        media_type="text/xml; charset=utf-8",
    )


@router.post("/dlna/control/content_directory", include_in_schema=False)
async def control_content_directory(request: Request) -> Response:
    library = request.app.state.dlna_library
    body = await request.body()
    action = _soap_action(request)
    params = _parse_soap_params(body)

    if action == "Browse":
        object_id = params.get("ObjectID", "0")
        browse_flag = params.get("BrowseFlag", "BrowseDirectChildren")
        starting_index = int(params.get("StartingIndex", "0"))
        requested_count = int(params.get("RequestedCount", "0"))
        result_xml, number_returned, total_matches = library.browse(
            object_id=object_id,
            browse_flag=browse_flag,
            starting_index=starting_index,
            requested_count=requested_count,
        )
        response = _soap_envelope(
            "u:BrowseResponse",
            {
                "Result": result_xml,
                "NumberReturned": str(number_returned),
                "TotalMatches": str(total_matches),
                "UpdateID": "1",
            },
            service_type=_CONTENT_DIRECTORY_TYPE,
        )
        return _xml_response(response)

    if action == "GetSearchCapabilities":
        return _xml_response(
            _soap_envelope(
                "u:GetSearchCapabilitiesResponse",
                {"SearchCaps": "dc:title,dc:creator,upnp:class"},
                service_type=_CONTENT_DIRECTORY_TYPE,
            )
        )

    if action == "GetSortCapabilities":
        return _xml_response(
            _soap_envelope(
                "u:GetSortCapabilitiesResponse",
                {"SortCaps": "dc:title"},
                service_type=_CONTENT_DIRECTORY_TYPE,
            )
        )

    if action == "GetSystemUpdateID":
        return _xml_response(
            _soap_envelope(
                "u:GetSystemUpdateIDResponse",
                {"Id": "1"},
                service_type=_CONTENT_DIRECTORY_TYPE,
            )
        )

    if action == "Search":
        object_id = params.get("ContainerID", "0")
        criteria = params.get("SearchCriteria", "*")
        starting_index = int(params.get("StartingIndex", "0"))
        requested_count = int(params.get("RequestedCount", "0"))
        result_xml, number_returned, total_matches = library.search(
            object_id=object_id,
            criteria=criteria,
            starting_index=starting_index,
            requested_count=requested_count,
        )
        response = _soap_envelope(
            "u:SearchResponse",
            {
                "Result": result_xml,
                "NumberReturned": str(number_returned),
                "TotalMatches": str(total_matches),
                "UpdateID": "1",
            },
            service_type=_CONTENT_DIRECTORY_TYPE,
        )
        return _xml_response(response)

    raise HTTPException(status_code=400, detail=f"Unsupported SOAP action: {action}")


@router.post("/dlna/control/connection_manager", include_in_schema=False)
async def control_connection_manager(request: Request) -> Response:
    action = _soap_action(request)

    if action == "GetProtocolInfo":
        response = _soap_envelope(
            "u:GetProtocolInfoResponse",
            {
                "Source": ",".join(
                    [
                        _protocol_info("video/mp4"),
                        _protocol_info("video/x-matroska"),
                        _protocol_info("video/mp2t"),
                    ]
                ),
                "Sink": "",
            },
            service_type=_CONNECTION_MANAGER_TYPE,
        )
        return _xml_response(response)

    if action == "GetCurrentConnectionIDs":
        return _xml_response(
            _soap_envelope(
                "u:GetCurrentConnectionIDsResponse",
                {"ConnectionIDs": "0"},
                service_type=_CONNECTION_MANAGER_TYPE,
            )
        )

    if action == "GetCurrentConnectionInfo":
        return _xml_response(
            _soap_envelope(
                "u:GetCurrentConnectionInfoResponse",
                {
                    "RcsID": "-1",
                    "AVTransportID": "-1",
                    "ProtocolInfo": "",
                    "PeerConnectionManager": "",
                    "PeerConnectionID": "-1",
                    "Direction": "Output",
                    "Status": "OK",
                },
                service_type=_CONNECTION_MANAGER_TYPE,
            )
        )

    raise HTTPException(status_code=400, detail=f"Unsupported SOAP action: {action}")


@router.route(
    "/dlna/event/{service_name}",
    methods=["SUBSCRIBE", "UNSUBSCRIBE"],
    include_in_schema=False,
)
async def event_stub(service_name: str) -> PlainTextResponse:
    _ = service_name
    return PlainTextResponse(
        "",
        status_code=200,
        headers={
            "SID": "uuid:cloudpop-event",
            "TIMEOUT": "Second-1800",
        },
    )


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _content_directory_scpd() -> str:
    return """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <actionList>
    <action>
      <name>Browse</name>
      <argumentList>
        <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
        <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
        <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
        <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
        <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
        <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
        <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>Search</name>
      <argumentList>
        <argument><name>ContainerID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
        <argument><name>SearchCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SearchCriteria</relatedStateVariable></argument>
        <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
        <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
        <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
        <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
        <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSearchCapabilities</name>
      <argumentList>
        <argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSortCapabilities</name>
      <argumentList>
        <argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSystemUpdateID</name>
      <argumentList>
        <argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetFeatureList</name>
      <argumentList>
        <argument><name>FeatureList</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_FeatureList</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetServiceResetToken</name>
      <argumentList>
        <argument><name>ResetToken</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ResetToken</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType><allowedValueList><allowedValue>BrowseMetadata</allowedValue><allowedValue>BrowseDirectChildren</allowedValue></allowedValueList></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SearchCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_FeatureList</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ResetToken</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>ContainerUpdateIDs</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>
"""


def _connection_manager_scpd() -> str:
    return """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <actionList>
    <action>
      <name>GetProtocolInfo</name>
      <argumentList>
        <argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>
        <argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetCurrentConnectionIDs</name>
      <argumentList>
        <argument><name>ConnectionIDs</name><direction>out</direction><relatedStateVariable>CurrentConnectionIDs</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetCurrentConnectionInfo</name>
      <argumentList>
        <argument><name>ConnectionID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
        <argument><name>RcsID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_RcsID</relatedStateVariable></argument>
        <argument><name>AVTransportID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_AVTransportID</relatedStateVariable></argument>
        <argument><name>ProtocolInfo</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ProtocolInfo</relatedStateVariable></argument>
        <argument><name>PeerConnectionManager</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionManager</relatedStateVariable></argument>
        <argument><name>PeerConnectionID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
        <argument><name>Direction</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Direction</relatedStateVariable></argument>
        <argument><name>Status</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionStatus</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>CurrentConnectionIDs</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_RcsID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_AVTransportID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionStatus</name><dataType>string</dataType><allowedValueList><allowedValue>OK</allowedValue><allowedValue>ContentFormatMismatch</allowedValue><allowedValue>InsufficientBandwidth</allowedValue><allowedValue>UnreliableChannel</allowedValue><allowedValue>Unknown</allowedValue></allowedValueList></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionManager</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Direction</name><dataType>string</dataType><allowedValueList><allowedValue>Input</allowedValue><allowedValue>Output</allowedValue></allowedValueList></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ProtocolInfo</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>
"""


def _soap_action(request: Request) -> str:
    header = request.headers.get("SOAPACTION", "").strip().strip('"')
    return header.rsplit("#", 1)[-1]


def _parse_soap_params(body: bytes) -> dict[str, str]:
    root = ET.fromstring(body)
    params: dict[str, str] = {}
    for elem in root.iter():
        if elem is root:
            continue
        tag = elem.tag.split("}", 1)[-1]
        if elem.text is not None:
            params[tag] = elem.text
    return params


def _soap_envelope(action: str, values: dict[str, str], service_type: str) -> str:
    body = "".join(f"<{name}>{escape(value)}</{name}>" for name, value in values.items())
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><{action} xmlns:u="{service_type}">{body}</{action}>'
        "</s:Body></s:Envelope>"
    )


def _protocol_info(mime_type: str) -> str:
    return (
        f"http-get:*:{mime_type}:"
        "DLNA.ORG_OP=01;"
        "DLNA.ORG_CI=0;"
        "DLNA.ORG_FLAGS=01700000000000000000000000000000"
    )


def _xml_response(content: str) -> Response:
    return Response(content=content, media_type="text/xml; charset=utf-8")
