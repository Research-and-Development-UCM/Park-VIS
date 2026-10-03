"""MQTT channel — publish to a configured broker/topic.

The channel config is always per-channel (no system default broker):

  {
      "broker": "tcp://broker.example.com",   # or mqtts://
      "port": 1883,                            # optional, default 1883
      "username": "...",
      "password": "...",
      "topic": "park-vis/alerts",
      "qos": 1,                                # optional, default 1
      "client_id": "park-vis-alerts"         # optional
  }

The full alert envelope is JSON-encoded and published to ``topic``.
The envelope shape is identical to the webhook channel (see
``channels/webhook.py``) so receivers can share parsing code.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Optional

import paho.mqtt.client as mqtt
from urllib.parse import urlparse

from ...logging_config import vulture_logger as logger


@dataclass
class MQTTResult:
    success: bool
    error: Optional[str] = None


def _split_broker(broker: str) -> tuple[str, int, bool]:
    """Parse a broker URL into (host, port, use_tls).

    Accepts ``tcp://host:port`` (default 1883) and ``mqtts://host:port``
    (default 8883).  A bare hostname is treated as tcp://.
    """
    if "://" not in broker:
        broker = "tcp://" + broker
    parsed = urlparse(broker)
    scheme = parsed.scheme or "tcp"
    use_tls = scheme in ("mqtts", "ssl")
    host = parsed.hostname or ""
    port = parsed.port or (8883 if use_tls else 1883)
    return host, port, use_tls


def _publish_sync(broker: str, port: int, username: str, password: str,
                  topic: str, payload_bytes: bytes, qos: int,
                  client_id: str, use_tls: bool, timeout: float) -> None:
    """Blocking MQTT publish.  Raises on connection / publish error.

    paho-mqtt's ``loop_start()`` runs its own network thread, so
    this function is safe to call from a thread pool executor
    (no event loop is involved).  Do NOT call
    ``asyncio.get_event_loop()`` here — that raises
    "There is no current event loop" in a thread without a loop.
    """
    client = mqtt.Client(
        client_id=client_id,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    if username:
        client.username_pw_set(username, password)
    if use_tls:
        client.tls_set()

    client.connect(broker, port, keepalive=30)
    client.loop_start()
    try:
        info = client.publish(topic, payload_bytes, qos=qos)
        info.wait_for_publish(timeout=timeout)
        if info.is_published() is False:
            raise RuntimeError("publish did not complete within timeout")
        # rc 0 = success in paho v2
        if info.rc and info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"mqtt publish rc={info.rc}")
    finally:
        client.loop_stop()
        client.disconnect()


async def deliver(config: dict, payload: dict, *, timeout: float = 10.0) -> MQTTResult:
    """Publish ``payload`` (JSON-encoded) to ``config['topic']``."""
    broker = (config or {}).get("broker", "")
    topic = (config or {}).get("topic", "")
    if not broker:
        return MQTTResult(success=False, error="missing broker in channel config")
    if not topic:
        return MQTTResult(success=False, error="missing topic in channel config")
    host, port, use_tls = _split_broker(broker)
    if not host:
        return MQTTResult(success=False, error="invalid broker URL")
    username = (config or {}).get("username", "") or ""
    password = (config or {}).get("password", "") or ""
    qos = int((config or {}).get("qos", 1) or 1)
    if qos not in (0, 1, 2):
        qos = 1
    # MQTT spec: duplicate client_ids disconnect the prior session.
    # Suffix the default with a random per-process tag so two Lot
    # Vulture instances (or two channels on the same broker) don't
    # kick each other off. Operator-supplied client_id is used as-is.
    if (config or {}).get("client_id"):
        client_id = config["client_id"]
    else:
        client_id = f"park-vis-alerts-{uuid.uuid4().hex[:8]}"
    body = json.dumps(payload, default=str).encode("utf-8")

    try:
        await asyncio.to_thread(
            _publish_sync, host, port, username, password,
            topic, body, qos, client_id, use_tls, timeout,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("MQTT publish failed: {err}", err=exc)
        return MQTTResult(success=False, error=f"mqtt: {exc!s}")
    except Exception as exc:  # noqa: BLE001
        logger.error("MQTT publish raised: {err}", err=exc)
        return MQTTResult(success=False, error=f"unexpected: {exc!s}")

    return MQTTResult(success=True)
