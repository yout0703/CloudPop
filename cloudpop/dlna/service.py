"""Minimal DLNA/UPnP SSDP service for device discovery."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from dataclasses import dataclass
from email.utils import formatdate
import time
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)

_SSDP_ADDR = "239.255.255.250"
_SSDP_PORT = 1900
_SERVER_HEADER = "CloudPop/0.1 UPnP/1.0 DLNADOC/1.50"


@dataclass(frozen=True)
class DlnaDeviceInfo:
    friendly_name: str
    uuid: str
    location: str
    advertise_interval_seconds: int = 30
    boot_id: int = 1
    config_id: int = 1
    nls: str = ""

    @property
    def usn(self) -> str:
        return f"uuid:{self.uuid}"

    def search_targets(self) -> tuple[str, ...]:
        return (
            "upnp:rootdevice",
            "urn:schemas-upnp-org:device:MediaServer:1",
            "urn:schemas-upnp-org:service:ContentDirectory:1",
            "urn:schemas-upnp-org:service:ConnectionManager:1",
            self.usn,
        )

    def location_host(self) -> str:
        parsed = urlsplit(self.location)
        return parsed.hostname or "127.0.0.1"


class SsdpProtocol(asyncio.DatagramProtocol):
    """Handle SSDP discovery packets and reply to M-SEARCH."""

    def __init__(self, device: DlnaDeviceInfo) -> None:
        self._device = device
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        logger.info("DLNA SSDP listener ready on %s:%d", _SSDP_ADDR, _SSDP_PORT)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            message = data.decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            return
        if "M-SEARCH * HTTP/1.1" not in message:
            return
        headers = _parse_ssdp_headers(message)
        if headers.get("man", "").lower() != '"ssdp:discover"':
            return
        st = headers.get("st", "").strip()
        normalized_targets = {target.lower(): target for target in self._device.search_targets()}
        if st.lower() != "ssdp:all" and st.lower() not in normalized_targets:
            return
        if not self.transport:
            return
        logger.info("DLNA SSDP M-SEARCH from %s ST=%s", addr[0], st or "-")
        if st.lower() == "ssdp:all":
            response_targets = self._device.search_targets()
        else:
            response_targets = (normalized_targets[st.lower()],)
        for target in response_targets:
            payload = build_ssdp_discovery_response(self._device, target).encode("utf-8")
            self.transport.sendto(payload, addr)
            logger.debug("DLNA SSDP replied to %s for ST=%s", addr[0], target)

    def error_received(self, exc: Exception) -> None:
        logger.debug("DLNA SSDP error: %s", exc)


class DlnaDiscoveryService:
    """Background SSDP service with periodic NOTIFY alive advertisements."""

    def __init__(self, device: DlnaDeviceInfo) -> None:
        self._device = device
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: SsdpProtocol | None = None
        self._notify_task: asyncio.Task[None] | None = None
        self._startup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        sock = _create_ssdp_socket(self._device.location_host())
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: SsdpProtocol(self._device),
            sock=sock,
        )
        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]
        self._notify_task = asyncio.create_task(self._notify_loop())
        self._startup_task = asyncio.create_task(self._startup_burst())
        await self._send_notify("ssdp:alive")

    async def stop(self) -> None:
        if self._startup_task:
            self._startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._startup_task
        if self._notify_task:
            self._notify_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._notify_task
        await self._send_notify("ssdp:byebye")
        if self._transport:
            self._transport.close()
            self._transport = None

    async def _notify_loop(self) -> None:
        while True:
            await asyncio.sleep(self._device.advertise_interval_seconds)
            await self._send_notify("ssdp:alive")

    async def _startup_burst(self) -> None:
        """Send extra alive bursts so clients that scan late can still latch on."""
        for delay in (0.5, 1.5, 3.0):
            await asyncio.sleep(delay)
            await self._send_notify("ssdp:alive")

    async def _send_notify(self, nts: str) -> None:
        if not self._transport:
            return
        for target in self._device.search_targets():
            payload = build_ssdp_notify(self._device, nts, target).encode("utf-8")
            self._transport.sendto(payload, (_SSDP_ADDR, _SSDP_PORT))
        logger.debug("DLNA SSDP sent %s notifications", nts)


def build_ssdp_discovery_response(device: DlnaDeviceInfo, search_target: str) -> str:
    headers = [
        "HTTP/1.1 200 OK",
        f"DATE: {formatdate(usegmt=True)}",
        "CACHE-CONTROL: max-age=1800",
        "EXT:",
        f"LOCATION: {device.location}",
        f"OPT: \"http://schemas.upnp.org/upnp/1/0/\"; ns=01",
        f"01-NLS: {device.nls}",
        f"BOOTID.UPNP.ORG: {device.boot_id}",
        f"CONFIGID.UPNP.ORG: {device.config_id}",
        f"SEARCHPORT.UPNP.ORG: {_SSDP_PORT}",
        "X-User-Agent: redsonic",
        f"SERVER: {_SERVER_HEADER}",
        f"ST: {search_target}",
        f"USN: {_usn_for_target(device, search_target)}",
        "",
        "",
    ]
    return "\r\n".join(headers)


def build_ssdp_notify(device: DlnaDeviceInfo, nts: str, search_target: str) -> str:
    headers = [
        "NOTIFY * HTTP/1.1",
        f"HOST: {_SSDP_ADDR}:{_SSDP_PORT}",
        "CACHE-CONTROL: max-age=1800",
        f"LOCATION: {device.location}",
        f"OPT: \"http://schemas.upnp.org/upnp/1/0/\"; ns=01",
        f"01-NLS: {device.nls}",
        f"NT: {search_target}",
        f"NTS: {nts}",
        f"BOOTID.UPNP.ORG: {device.boot_id}",
        f"CONFIGID.UPNP.ORG: {device.config_id}",
        f"SEARCHPORT.UPNP.ORG: {_SSDP_PORT}",
        "X-User-Agent: redsonic",
        f"SERVER: {_SERVER_HEADER}",
        f"USN: {_usn_for_target(device, search_target)}",
        "",
        "",
    ]
    return "\r\n".join(headers)


def build_dlna_device_info(
    *,
    friendly_name: str,
    uuid: str,
    base_url: str,
    http_path: str,
    advertise_interval_seconds: int,
) -> DlnaDeviceInfo:
    location = f"{base_url.rstrip('/')}{http_path}"
    boot_id = int(time.time())
    return DlnaDeviceInfo(
        friendly_name=friendly_name,
        uuid=uuid,
        location=location,
        advertise_interval_seconds=advertise_interval_seconds,
        boot_id=boot_id,
        config_id=1,
        nls=uuid,
    )


def _parse_ssdp_headers(message: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in message.split("\r\n")[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def _usn_for_target(device: DlnaDeviceInfo, target: str) -> str:
    if target == device.usn:
        return device.usn
    return f"{device.usn}::{target}"


def _create_ssdp_socket(interface_ip: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", _SSDP_PORT))
    local_ip = interface_ip or "0.0.0.0"
    mreq = socket.inet_aton(_SSDP_ADDR) + socket.inet_aton(local_ip)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    if interface_ip and interface_ip != "127.0.0.1":
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
    return sock
