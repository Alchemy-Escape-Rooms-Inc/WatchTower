#!/usr/bin/env python3
"""
Alchemy Escape Room System Checker v2
======================================
Active ping-based verification of BAC controllers and ESP32 microcontrollers.
Clean card-based dashboard UI.

Author: Built for Alchemy Escape Rooms Inc.
"""

import json
import time
import threading
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable
from enum import Enum
import paho.mqtt.client as mqtt
from flask import Flask, render_template_string, jsonify, request
import logging
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeviceStatus(Enum):
    UNKNOWN = "unknown"     # Not tested yet
    ONLINE = "online"       # Responded to ping
    OFFLINE = "offline"     # No response to ping
    TESTING = "testing"     # Currently being tested


class DeviceType(Enum):
    BAC = "bac"
    ESP32 = "esp32"


@dataclass
class Device:
    name: str
    device_type: DeviceType
    topic_base: str
    icon: str = "🔌"
    color: str = "#4A90D9"  # Default blue
    status: DeviceStatus = DeviceStatus.UNKNOWN
    last_test: Optional[datetime] = None
    response_time_ms: Optional[int] = None
    last_error: Optional[str] = None
    commands: list = field(default_factory=lambda: ["PING", "STATUS", "RESET", "PUZZLE_RESET"])
    needs_protocol: bool = False


class SystemChecker:
    def __init__(self, config_file: str = "config.json"):
        self.devices: Dict[str, Device] = {}
        self.mqtt_client: Optional[mqtt.Client] = None
        self.mqtt_connected = False
        self.broker_host = "10.1.10.115"
        self.broker_port = 1883
        self.lock = threading.Lock()

        # For tracking ping responses
        self.pending_pings: Dict[str, dict] = {}  # ping_id -> {device_name, start_time}
        self.ping_timeout_esp32 = 3.0  # seconds for ESP32 (PING/PONG is fast)
        self.ping_timeout_bac = 15.0   # seconds for BAC (heartbeat every ~10 sec)

        # Message log for display panel
        self.message_log: list = []
        self.max_messages = 100

        # Track recently sent messages to filter echoes
        self.recent_sent: list = []
        self.max_recent_sent = 20

        # Track last values for delta filtering (only show changes > threshold)
        self.last_values: Dict[str, float] = {}
        self.delta_threshold = 2  # degrees - only show if change is greater than this
        self.delta_topics = ["/Hor", "/Ver", "/angle", "/distance"]  # topics to apply delta filtering

        # Topics to always filter out (noisy/not useful)
        self.hidden_topics = ["/heartbeat", "/get/heartbeat"]

        # Track last payload to filter repeated identical messages
        self.last_payloads: Dict[str, str] = {}
        self.dedup_topics = ["/Loaded", "/Fired", "/triggered"]  # topics to deduplicate

        self.load_config(config_file)
    
    def load_config(self, config_file: str):
        """Load device configuration from JSON file."""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            self.broker_host = config.get('broker_host', self.broker_host)
            self.broker_port = config.get('broker_port', self.broker_port)
            
            # Color palette
            colors = ["#4A90D9", "#D4A84B", "#45B7AA", "#D97B9F", "#7B68D9", "#5DB86C"]
            color_idx = 0
            
            # Load BAC controllers
            for bac in config.get('bac_controllers', []):
                device = Device(
                    name=bac['name'],
                    device_type=DeviceType.BAC,
                    topic_base=bac['name'],
                    icon=bac.get('icon', '🎛️'),
                    color=bac.get('color', colors[color_idx % len(colors)])
                )
                self.devices[bac['name']] = device
                color_idx += 1
                logger.info(f"Loaded BAC: {bac['name']}")
            
            # Load ESP32 devices
            for esp in config.get('esp32_devices', []):
                device = Device(
                    name=esp['name'],
                    device_type=DeviceType.ESP32,
                    topic_base=esp['topic'],
                    icon=esp.get('icon', '📡'),
                    color=esp.get('color', colors[color_idx % len(colors)]),
                    commands=esp.get('commands', ["PING", "STATUS", "RESET", "PUZZLE_RESET"]),
                    needs_protocol=esp.get('needs_protocol', False)
                )
                self.devices[esp['name']] = device
                color_idx += 1
                logger.info(f"Loaded ESP32: {esp['name']} (needs_protocol={device.needs_protocol})")
                
            logger.info(f"Loaded {len(self.devices)} devices from config")
            
        except FileNotFoundError:
            logger.warning(f"Config file {config_file} not found, using defaults")
            self._load_default_config()
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file: {e}")
            self._load_default_config()
    
    def _load_default_config(self):
        """Load default device configuration for Mermaid's Tale."""
        colors = ["#4A90D9", "#D4A84B", "#45B7AA", "#D97B9F"]
        
        # BAC Controllers
        bacs = [
            ("Shattic", "🚢", colors[0]),
            ("Captain", "🎖️", colors[1]),
            ("Cove", "🏝️", colors[2]),
            ("Jungle", "🌴", colors[3]),
        ]
        for name, icon, color in bacs:
            self.devices[name] = Device(
                name=name,
                device_type=DeviceType.BAC,
                topic_base=name,
                icon=icon,
                color=color
            )
        
        # ESP32 Devices
        esp32s = [
            ("ShipMotion1", "ShipMotion1", "🚢", "#4A90D9"),
            ("ShipMotion2", "ShipMotion2", "🚢", "#4A90D9"),
            ("ShipMotion3", "ShipMotion3", "🚢", "#4A90D9"),
            ("BarrelPiston", "BarrelPiston", "🛢️", "#D4A84B"),
            ("JungleDoor", "JungleDoor", "🚪", "#45B7AA"),
            ("JungleMotion1", "JungleMotion1", "🌿", "#45B7AA"),
            ("JungleMotion2", "JungleMotion2", "🌿", "#45B7AA"),
            ("JungleMotion3", "JungleMotion3", "🌿", "#45B7AA"),
            ("Driftwood", "Driftwood", "🪵", "#D97B9F"),
            ("Hieroglyphics", "Hieroglyphics", "📜", "#7B68D9"),
            ("TridentReveal", "TridentReveal", "🔱", "#4A90D9"),
            ("SeaShells", "SeaShells", "🐚", "#D97B9F"),
            ("StarCharts", "StarCharts", "⭐", "#D4A84B"),
        ]
        for name, topic, icon, color in esp32s:
            self.devices[name] = Device(
                name=name,
                device_type=DeviceType.ESP32,
                topic_base=topic,
                icon=icon,
                color=color
            )
        
        logger.info(f"Loaded {len(self.devices)} devices from defaults")
    
    def connect_mqtt(self):
        """Connect to MQTT broker."""
        try:
            self.mqtt_client = mqtt.Client(client_id=f"alchemy_checker_{uuid.uuid4().hex[:8]}")
            self.mqtt_client.on_connect = self._on_connect
            self.mqtt_client.on_disconnect = self._on_disconnect
            self.mqtt_client.on_message = self._on_message
            
            logger.info(f"Connecting to MQTT broker at {self.broker_host}:{self.broker_port}")
            self.mqtt_client.connect(self.broker_host, self.broker_port, 60)
            self.mqtt_client.loop_start()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self.mqtt_connected = True
            logger.info("Connected to MQTT broker")
            
            # Subscribe to response topics
            # BAC heartbeats and events - try multiple patterns
            client.subscribe("+/get/#")           # {name}/get/anything
            client.subscribe("+/+/get/#")         # {prefix}/{name}/get/anything
            client.subscribe("BAC/+/get/#")       # BAC/{name}/get/anything
            # ESP32 responses - listen on command topic for PONG responses
            client.subscribe("MermaidsTale/+/status")
            client.subscribe("MermaidsTale/+/command")
            # Wildcard to see all traffic (for debugging)
            client.subscribe("#")

            logger.info("Subscribed to all topics for debugging")
        else:
            logger.error(f"MQTT connection failed with code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback."""
        self.mqtt_connected = False
        logger.warning(f"Disconnected from MQTT broker (rc={rc})")
    
    def log_message(self, direction: str, topic: str, payload: str, device: str = None):
        """Add a message to the log buffer."""
        with self.lock:
            # Track sent messages to filter echoes
            if direction == "TX":
                self.recent_sent.insert(0, (topic, payload))
                if len(self.recent_sent) > self.max_recent_sent:
                    self.recent_sent.pop()

            msg = {
                "timestamp": datetime.now().isoformat(),
                "direction": direction,  # "TX" or "RX"
                "topic": topic,
                "payload": payload[:200] if payload else "",
                "device": device
            }
            self.message_log.insert(0, msg)
            if len(self.message_log) > self.max_messages:
                self.message_log.pop()

    def is_echo(self, topic: str, payload: str) -> bool:
        """Check if this message is an echo of something we just sent."""
        with self.lock:
            for sent_topic, sent_payload in self.recent_sent:
                if sent_topic == topic and sent_payload == payload:
                    # Remove it so we only filter once
                    self.recent_sent.remove((sent_topic, sent_payload))
                    return True
            return False

    def is_hidden_topic(self, topic: str) -> bool:
        """Check if this topic should always be hidden."""
        return any(pattern in topic for pattern in self.hidden_topics)

    def is_duplicate_message(self, topic: str, payload: str) -> bool:
        """Check if this is a repeated message (same payload as last time)."""
        # Only check topics that tend to repeat
        is_dedup_topic = any(pattern in topic for pattern in self.dedup_topics)
        if not is_dedup_topic:
            return False

        with self.lock:
            last_payload = self.last_payloads.get(topic)
            self.last_payloads[topic] = payload

            if last_payload == payload:
                return True  # Duplicate - filter it
            return False  # New value - show it

    def should_filter_by_delta(self, topic: str, payload: str) -> bool:
        """Check if this message should be filtered based on delta threshold."""
        # Only apply delta filtering to specific topics
        is_delta_topic = any(pattern in topic for pattern in self.delta_topics)
        if not is_delta_topic:
            return False  # Don't filter - not a delta topic

        # Try to extract numeric value from payload (e.g., "pre_150" -> 150)
        try:
            # Handle formats like "pre_150", "150", "angle: 150"
            import re
            numbers = re.findall(r'[-+]?\d*\.?\d+', payload)
            if not numbers:
                return False  # Can't parse - don't filter
            value = float(numbers[-1])  # Take last number found
        except (ValueError, IndexError):
            return False  # Can't parse - don't filter

        # Check if value changed enough from last time
        with self.lock:
            last_value = self.last_values.get(topic)
            if last_value is None:
                # First time seeing this topic - show it and store
                self.last_values[topic] = value
                return False  # Don't filter

            delta = abs(value - last_value)
            if delta >= self.delta_threshold:
                # Significant change - show it and update stored value
                self.last_values[topic] = value
                return False  # Don't filter

            # Small change - filter it out
            return True

    def get_messages(self, limit: int = 50) -> list:
        """Get recent messages from the log."""
        with self.lock:
            return self.message_log[:limit]

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages - only care about ping responses."""
        topic = msg.topic
        try:
            payload = msg.payload.decode('utf-8').strip()
        except:
            payload = str(msg.payload)

        # Log to message buffer (only device-related topics, with filtering)
        if "MermaidsTale" in topic or any(d in topic for d in ["Shattic", "Captain", "Cove", "Jungle"]):
            # Skip if this is a hidden topic (heartbeats, etc.)
            if self.is_hidden_topic(topic):
                pass  # Skip - hidden topic
            # Skip if this is an echo of a message we just sent
            elif self.is_echo(topic, payload):
                pass  # Skip echo
            # Skip if this is a duplicate of the last message
            elif self.is_duplicate_message(topic, payload):
                pass  # Skip - duplicate
            # Skip if change is too small (delta filtering for position data)
            elif self.should_filter_by_delta(topic, payload):
                pass  # Skip - change too small
            else:
                # Extract device name from topic
                parts = topic.split("/")
                device_name = parts[1] if len(parts) > 1 else None
                self.log_message("RX", topic, payload, device_name)

        # DEBUG: Log all incoming messages
        logger.info(f"MQTT RX: {topic} = {payload[:50] if payload else '(empty)'}")

        now = datetime.now()

        with self.lock:
            # Check if this is a response we're waiting for
            for device_name, device in self.devices.items():
                if device.status != DeviceStatus.TESTING:
                    continue

                # Check if topic matches this device's response pattern
                is_match = False

                if device.device_type == DeviceType.BAC:
                    # BAC responds on {name}/get/... (case-insensitive check)
                    expected_prefix = f"{device.topic_base}/get/"
                    if topic.lower().startswith(expected_prefix.lower()):
                        is_match = True
                    # Also check if topic contains the BAC name anywhere (fallback)
                    elif device.topic_base.lower() in topic.lower() and "/get/" in topic.lower():
                        is_match = True
                        logger.info(f"BAC {device_name}: matched via fallback on '{topic}'")

                elif device.device_type == DeviceType.ESP32:
                    # ESP32 responds with PONG on MermaidsTale/{topic}/command
                    expected_topic = f"MermaidsTale/{device.topic_base}/command"
                    if topic == expected_topic and payload == "PONG":
                        is_match = True
                    # Also accept status messages as proof of life
                    elif topic == f"MermaidsTale/{device.topic_base}/status":
                        is_match = True
                    else:
                        logger.debug(f"ESP32 {device_name}: topic '{topic}' != '{expected_topic}' or payload '{payload}' != 'PONG'")

                if is_match:
                    # Calculate response time
                    if device.last_test:
                        response_ms = int((now - device.last_test).total_seconds() * 1000)
                        device.response_time_ms = response_ms

                    device.status = DeviceStatus.ONLINE
                    device.last_error = None
                    logger.info(f"✓ {device_name} responded ({device.response_time_ms}ms) - {payload[:20]}")
                    return
    
    def ping_device(self, device_name: str) -> bool:
        """Send a ping to a specific device and wait for response."""
        if device_name not in self.devices:
            logger.warning(f"Unknown device: {device_name}")
            return False
        
        if not self.mqtt_connected:
            logger.error("Cannot ping - MQTT not connected")
            return False
        
        device = self.devices[device_name]
        
        with self.lock:
            device.status = DeviceStatus.TESTING
            device.last_test = datetime.now()
            device.response_time_ms = None
        
        # Send ping based on device type
        if device.device_type == DeviceType.BAC:
            # BACs don't have PING/PONG - they send heartbeats every 10 seconds
            # on {name}/get/heartbeat. Just wait for the next heartbeat.
            logger.info(f"→ Waiting for BAC {device_name} heartbeat (sent every ~10 sec)")
            
        elif device.device_type == DeviceType.ESP32:
            # For ESP32s, send PING command
            topic = f"MermaidsTale/{device.topic_base}/command"
            self.mqtt_client.publish(topic, "PING")
            logger.info(f"→ Pinged ESP32 {device_name} on {topic}")
        
        return True
    
    def ping_all_devices(self):
        """Ping all devices."""
        for name in self.devices:
            self.ping_device(name)
    
    def check_timeouts(self):
        """Mark devices as offline if they didn't respond in time."""
        now = datetime.now()

        with self.lock:
            for device in self.devices.values():
                if device.status == DeviceStatus.TESTING and device.last_test:
                    elapsed = (now - device.last_test).total_seconds()
                    # Use different timeout for BAC vs ESP32
                    timeout = self.ping_timeout_bac if device.device_type == DeviceType.BAC else self.ping_timeout_esp32
                    if elapsed > timeout:
                        device.status = DeviceStatus.OFFLINE
                        device.last_error = "No response"
                        logger.warning(f"✗ {device.name} timed out after {timeout}s")
    
    def get_status_summary(self) -> dict:
        """Get summary of all device statuses."""
        self.check_timeouts()
        
        summary = {
            "broker_connected": self.mqtt_connected,
            "broker_host": self.broker_host,
            "broker_port": self.broker_port,
            "timestamp": datetime.now().isoformat(),
            "devices": {},
            "counts": {
                "online": 0,
                "offline": 0,
                "unknown": 0,
                "testing": 0
            }
        }
        
        with self.lock:
            for name, device in self.devices.items():
                summary["devices"][name] = {
                    "type": device.device_type.value,
                    "status": device.status.value,
                    "icon": device.icon,
                    "color": device.color,
                    "topic": device.topic_base,
                    "last_test": device.last_test.isoformat() if device.last_test else None,
                    "response_ms": device.response_time_ms,
                    "error": device.last_error,
                    "commands": device.commands,
                    "needs_protocol": device.needs_protocol
                }
                summary["counts"][device.status.value] += 1
        
        return summary


# Flask Web Dashboard
app = Flask(__name__)
checker: Optional[SystemChecker] = None

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alchemy System Check</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body {
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
            min-height: 100vh;
            padding: 30px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        /* Header */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding: 20px 30px;
            background: white;
            border-radius: 16px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
        }
        
        .header h1 {
            font-size: 24px;
            font-weight: 600;
            color: #2c3e50;
        }
        
        .header h1 span {
            color: #4A90D9;
        }
        
        .broker-badge {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 500;
        }
        
        .broker-badge.connected {
            background: #e8f5e9;
            color: #2e7d32;
        }
        
        .broker-badge.disconnected {
            background: #ffebee;
            color: #c62828;
        }
        
        .broker-badge .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }
        
        .broker-badge.connected .dot { background: #4caf50; }
        .broker-badge.disconnected .dot { background: #f44336; }
        
        /* Controls */
        .controls {
            display: flex;
            gap: 12px;
            margin-bottom: 30px;
        }
        
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .btn:active { transform: translateY(0); }
        
        .btn-primary {
            background: linear-gradient(135deg, #4A90D9, #357ABD);
            color: white;
        }
        
        .btn-secondary {
            background: white;
            color: #555;
            border: 2px solid #e0e0e0;
        }
        
        /* Section */
        .section {
            margin-bottom: 40px;
        }
        
        .section-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding-left: 5px;
        }
        
        .section-title {
            font-size: 18px;
            font-weight: 600;
            color: #2c3e50;
        }
        
        .section-count {
            background: #e3f2fd;
            color: #1976d2;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        
        /* Device Grid */
        .device-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 16px;
        }
        
        /* Device Card - 3D Flip Container */
        .device-card-container {
            perspective: 1000px;
            height: 220px;
        }

        .device-card {
            position: relative;
            width: 100%;
            height: 100%;
            transform-style: preserve-3d;
            transition: transform 0.5s;
        }

        .device-card-container:hover .device-card {
            transform: rotateY(180deg);
        }

        .device-card-container:hover .device-card.needs-protocol {
            transform: none;
        }

        .card-front, .card-back {
            position: absolute;
            width: 100%;
            height: 100%;
            backface-visibility: hidden;
            border-radius: 16px;
            background: white;
            overflow: hidden;
        }

        .card-front {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start;
            padding: 20px 16px 0;
            text-align: center;
            position: relative;
        }

        /* Decorative wave graphic at bottom of card */
        .card-wave {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            height: 50px;
            overflow: hidden;
        }

        .card-wave svg {
            width: 100%;
            height: 100%;
            opacity: 0.15;
        }

        .device-card.online .card-wave svg { fill: var(--card-color); opacity: 0.25; }
        .device-card.offline .card-wave svg { fill: #e57373; opacity: 0.2; }
        .device-card.testing .card-wave svg { fill: #42a5f5; opacity: 0.25; }
        .device-card.needs-protocol .card-wave svg { fill: #ff9800; opacity: 0.2; }

        .card-back {
            transform: rotateY(180deg);
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            overflow-y: auto;
        }

        .card-back-title {
            font-size: 12px;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 4px;
            text-align: center;
        }

        .cmd-btn {
            padding: 8px 12px;
            border: none;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            background: #f0f4f8;
            color: #2c3e50;
        }

        .cmd-btn:hover {
            background: var(--card-color);
            color: white;
            transform: scale(1.02);
        }

        .cmd-btn.primary {
            background: var(--card-color);
            color: white;
        }

        .card-front::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: var(--card-color);
            opacity: 0;
            transition: opacity 0.2s;
        }

        .device-card.online .card-front::before { opacity: 1; }

        /* Needs Protocol Badge */
        .needs-protocol-badge {
            position: absolute;
            top: 8px;
            right: 8px;
            background: #ff9800;
            color: white;
            font-size: 9px;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: 600;
        }

        .device-card.needs-protocol .card-front {
            opacity: 0.7;
        }
        
        .device-icon {
            width: 56px;
            height: 56px;
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 10px;
            font-size: 24px;
            transition: all 0.2s;
        }

        .device-card.online .device-icon {
            background: var(--card-color);
            color: white;
            box-shadow: 0 4px 12px var(--card-shadow);
        }

        .device-card.offline .device-icon {
            background: #ffebee;
            color: #e57373;
        }

        .device-card.unknown .device-icon {
            background: #f5f5f5;
            color: #9e9e9e;
        }

        .device-card.testing .device-icon {
            background: #e3f2fd;
            color: #42a5f5;
            animation: pulse 1s infinite;
        }

        .device-card.needs-protocol .device-icon {
            background: #fff3e0;
            color: #ff9800;
        }

        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.05); }
        }

        .device-name {
            font-size: 12px;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 3px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
        }

        .device-status {
            font-size: 10px;
            color: #999;
        }

        .device-card.online .device-status { color: #4caf50; }
        .device-card.offline .device-status { color: #e57373; }
        .device-card.testing .device-status { color: #42a5f5; }
        .device-card.needs-protocol .device-status { color: #ff9800; }

        .device-ping {
            font-size: 9px;
            color: #bbb;
            margin-top: 3px;
        }
        
        /* Status Bar */
        .status-bar {
            display: flex;
            gap: 20px;
            padding: 16px 24px;
            background: white;
            border-radius: 12px;
            margin-bottom: 30px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }
        
        .status-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }
        
        .status-dot.online { background: #4caf50; }
        .status-dot.offline { background: #f44336; }
        .status-dot.unknown { background: #9e9e9e; }
        .status-dot.testing { background: #2196f3; }
        
        .status-label {
            font-size: 14px;
            color: #555;
        }
        
        .status-count {
            font-weight: 700;
            color: #2c3e50;
        }
        
        /* Loading overlay */
        .loading-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(255,255,255,0.9);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        
        .loading-overlay.show { display: flex; }
        
        .loading-spinner {
            text-align: center;
        }
        
        .loading-spinner .icon {
            font-size: 48px;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        
        .loading-spinner p {
            margin-top: 16px;
            color: #555;
            font-size: 16px;
        }
        
        /* Last update */
        .last-update {
            text-align: center;
            color: #999;
            font-size: 12px;
            margin-top: 30px;
        }

        /* MQTT Display Panel - Sticky */
        .mqtt-panel {
            background: #1e1e1e;
            border-radius: 12px;
            margin-bottom: 20px;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
            position: sticky;
            top: 20px;
            z-index: 100;
        }

        .mqtt-panel-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 20px;
            background: #2d2d2d;
            border-bottom: 1px solid #3d3d3d;
        }

        .mqtt-panel-title {
            font-size: 14px;
            font-weight: 600;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .mqtt-panel-title .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #4caf50;
            animation: blink 1s infinite;
        }

        @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
        }

        .mqtt-panel-controls {
            display: flex;
            gap: 8px;
        }

        .mqtt-btn {
            padding: 6px 12px;
            border: none;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            background: #3d3d3d;
            color: #e0e0e0;
            transition: all 0.2s;
        }

        .mqtt-btn:hover {
            background: #4d4d4d;
        }

        .mqtt-btn.active {
            background: #4A90D9;
            color: white;
        }

        .mqtt-messages {
            height: 300px;
            overflow-y: auto;
            padding: 12px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 12px;
        }

        .mqtt-message {
            padding: 6px 10px;
            margin-bottom: 4px;
            border-radius: 4px;
            background: #2a2a2a;
            display: flex;
            gap: 12px;
            align-items: flex-start;
        }

        .mqtt-message:hover {
            background: #333;
        }

        .mqtt-time {
            color: #666;
            font-size: 10px;
            min-width: 70px;
        }

        .mqtt-dir {
            font-weight: 600;
            min-width: 24px;
        }

        .mqtt-dir.tx {
            color: #4fc3f7;
        }

        .mqtt-dir.rx {
            color: #81c784;
        }

        .mqtt-topic {
            color: #ffb74d;
            min-width: 200px;
            word-break: break-all;
        }

        .mqtt-payload {
            color: #e0e0e0;
            flex: 1;
            word-break: break-all;
        }

        .mqtt-empty {
            color: #666;
            text-align: center;
            padding: 40px;
        }

        /* Drag and Drop */
        .device-card-container {
            cursor: grab;
            transition: transform 0.2s, opacity 0.2s;
        }

        .device-card-container:active {
            cursor: grabbing;
        }

        .device-card-container.dragging {
            opacity: 0.5;
            transform: scale(1.05);
            z-index: 1000;
        }

        .device-card-container.drag-over {
            transform: scale(0.95);
            border: 2px dashed #4A90D9;
            border-radius: 18px;
        }

        .drag-placeholder {
            background: rgba(74, 144, 217, 0.1);
            border: 2px dashed #4A90D9;
            border-radius: 16px;
            height: 220px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔧 <span>Alchemy</span> System Check</h1>
            <div id="broker-badge" class="broker-badge disconnected">
                <span class="dot"></span>
                <span id="broker-text">Disconnected</span>
            </div>
        </div>
        
        <div class="status-bar">
            <div class="status-item">
                <span class="status-dot online"></span>
                <span class="status-label">Online</span>
                <span class="status-count" id="count-online">0</span>
            </div>
            <div class="status-item">
                <span class="status-dot offline"></span>
                <span class="status-label">Offline</span>
                <span class="status-count" id="count-offline">0</span>
            </div>
            <div class="status-item">
                <span class="status-dot unknown"></span>
                <span class="status-label">Not Tested</span>
                <span class="status-count" id="count-unknown">0</span>
            </div>
        </div>

        <div class="mqtt-panel">
            <div class="mqtt-panel-header">
                <div class="mqtt-panel-title">
                    <span class="dot"></span>
                    MQTT Message Log
                </div>
                <div class="mqtt-panel-controls">
                    <button class="mqtt-btn active" onclick="toggleAutoScroll()" id="auto-scroll-btn">Auto-Scroll</button>
                    <button class="mqtt-btn" onclick="clearMessages()">Clear</button>
                    <button class="mqtt-btn" onclick="refreshMessages()">Refresh</button>
                </div>
            </div>
            <div class="mqtt-messages" id="mqtt-messages">
                <div class="mqtt-empty">Click a command button to see MQTT traffic...</div>
            </div>
        </div>

        <div class="controls">
            <button class="btn btn-primary" onclick="testAll()">
                <span>📡</span> Test All Devices
            </button>
            <button class="btn btn-secondary" onclick="refreshStatus()">
                <span>🔄</span> Refresh
            </button>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-title">BAC Controllers</span>
                <span class="section-count" id="bac-count">0 devices</span>
            </div>
            <div id="bac-grid" class="device-grid"></div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-title">ESP32 Microcontrollers</span>
                <span class="section-count" id="esp32-count">0 devices</span>
            </div>
            <div id="esp32-grid" class="device-grid"></div>
        </div>
        
        <div class="last-update">
            Last updated: <span id="last-update">Never</span>
        </div>
    </div>
    
    <div id="loading" class="loading-overlay">
        <div class="loading-spinner">
            <div class="icon">⚡</div>
            <p>Testing devices...</p>
        </div>
    </div>
    
    <script>
        function createDeviceCard(name, device) {
            const pingText = device.response_ms ? `${device.response_ms}ms` : '';
            const needsProtocol = device.needs_protocol || false;
            const commands = device.commands || ['PING', 'STATUS', 'RESET', 'PUZZLE_RESET'];

            let statusText;
            if (needsProtocol) {
                statusText = 'Needs WatchTower';
            } else {
                statusText = {
                    'online': 'Online',
                    'offline': 'No Response',
                    'unknown': 'Not Tested',
                    'testing': 'Testing...'
                }[device.status] || device.status;
            }

            const statusClass = needsProtocol ? 'needs-protocol' : device.status;

            // Build command buttons for the back of the card
            const cmdButtons = commands.map(cmd => {
                const isPrimary = cmd === 'PING' || cmd === 'STATUS';
                return `<button class="cmd-btn ${isPrimary ? 'primary' : ''}"
                                onclick="sendCommand('${name}', '${cmd}'); event.stopPropagation();"
                                style="--card-color: ${device.color};">${cmd}</button>`;
            }).join('');

            // Wave SVG graphic
            const waveSvg = `<svg viewBox="0 0 200 50" preserveAspectRatio="none">
                <path d="M0,25 C40,45 60,5 100,25 C140,45 160,5 200,25 L200,50 L0,50 Z"/>
            </svg>`;

            return `
                <div class="device-card-container"
                     draggable="true"
                     data-device="${name}"
                     data-type="${device.type}"
                     ondragstart="handleDragStart(event)"
                     ondragend="handleDragEnd(event)"
                     ondragover="handleDragOver(event)"
                     ondragleave="handleDragLeave(event)"
                     ondrop="handleDrop(event)">
                    <div class="device-card ${statusClass}"
                         style="--card-color: ${device.color}; --card-shadow: ${device.color}40;">
                        <div class="card-front" onclick="testDevice('${name}')">
                            ${needsProtocol ? '<span class="needs-protocol-badge">NEEDS UPDATE</span>' : ''}
                            <div class="device-icon">${device.icon}</div>
                            <div class="device-name">${name}</div>
                            <div class="device-status">${statusText}</div>
                            ${pingText && !needsProtocol ? `<div class="device-ping">${pingText}</div>` : ''}
                            <div class="card-wave">${waveSvg}</div>
                        </div>
                        <div class="card-back" style="--card-color: ${device.color};">
                            <div class="card-back-title">${name}</div>
                            ${cmdButtons}
                        </div>
                    </div>
                </div>
            `;
        }

        async function sendCommand(deviceName, command) {
            try {
                const response = await fetch(`/api/command/${deviceName}/${command}`);
                const data = await response.json();
                console.log(`Command sent: ${command} to ${deviceName}`, data);

                // Refresh status after command
                setTimeout(refreshStatus, 500);
                setTimeout(refreshStatus, 1500);
            } catch (error) {
                console.error('Command failed:', error);
            }
        }
        
        async function refreshStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                // Update broker status
                const badge = document.getElementById('broker-badge');
                const badgeText = document.getElementById('broker-text');
                if (data.broker_connected) {
                    badge.className = 'broker-badge connected';
                    badgeText.textContent = `${data.broker_host}:${data.broker_port}`;
                } else {
                    badge.className = 'broker-badge disconnected';
                    badgeText.textContent = 'Disconnected';
                }
                
                // Update counts
                document.getElementById('count-online').textContent = data.counts.online;
                document.getElementById('count-offline').textContent = data.counts.offline;
                document.getElementById('count-unknown').textContent = data.counts.unknown;
                
                // Separate devices by type
                const bacDevices = [];
                const esp32Devices = [];

                for (const [name, device] of Object.entries(data.devices)) {
                    if (device.type === 'bac') {
                        bacDevices.push({ name, device });
                    } else {
                        esp32Devices.push({ name, device });
                    }
                }

                // Sort by saved order
                const sortedBac = sortDevicesByOrder(bacDevices.map(d => ({ name: d.name, ...d.device })), 'bac');
                const sortedEsp32 = sortDevicesByOrder(esp32Devices.map(d => ({ name: d.name, ...d.device })), 'esp32');

                // Render cards
                let bacHtml = '';
                let esp32Html = '';

                sortedBac.forEach(item => {
                    const deviceData = bacDevices.find(d => d.name === item.name)?.device || item.device || item;
                    bacHtml += createDeviceCard(item.name, deviceData);
                });

                sortedEsp32.forEach(item => {
                    const deviceData = esp32Devices.find(d => d.name === item.name)?.device || item.device || item;
                    esp32Html += createDeviceCard(item.name, deviceData);
                });

                document.getElementById('bac-grid').innerHTML = bacHtml;
                document.getElementById('esp32-grid').innerHTML = esp32Html;

                const bacCount = sortedBac.length;
                const esp32Count = sortedEsp32.length;
                document.getElementById('bac-count').textContent = `${bacCount} devices`;
                document.getElementById('esp32-count').textContent = `${esp32Count} devices`;
                
                document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
                
            } catch (error) {
                console.error('Failed to refresh:', error);
            }
        }
        
        async function testDevice(name) {
            try {
                await fetch(`/api/ping/${name}`);
                setTimeout(refreshStatus, 500);
                setTimeout(refreshStatus, 2000);
                setTimeout(refreshStatus, 4000);
            } catch (error) {
                console.error('Test failed:', error);
            }
        }
        
        async function testAll() {
            document.getElementById('loading').classList.add('show');
            try {
                await fetch('/api/ping-all');
                
                // Poll for updates
                for (let i = 0; i < 8; i++) {
                    setTimeout(refreshStatus, i * 500);
                }
                
                setTimeout(() => {
                    document.getElementById('loading').classList.remove('show');
                }, 4000);
                
            } catch (error) {
                console.error('Test all failed:', error);
                document.getElementById('loading').classList.remove('show');
            }
        }
        
        // Initial load
        refreshStatus();

        // Auto-refresh every 10 seconds
        setInterval(refreshStatus, 10000);

        // MQTT Message Panel
        let autoScroll = true;
        let messagePolling = null;

        function formatTime(isoString) {
            const date = new Date(isoString);
            return date.toLocaleTimeString('en-US', { hour12: false });
        }

        function renderMessages(messages) {
            const container = document.getElementById('mqtt-messages');
            if (!messages || messages.length === 0) {
                container.innerHTML = '<div class="mqtt-empty">No messages yet. Click a command button to see MQTT traffic...</div>';
                return;
            }

            const html = messages.map(msg => `
                <div class="mqtt-message">
                    <span class="mqtt-time">${formatTime(msg.timestamp)}</span>
                    <span class="mqtt-dir ${msg.direction.toLowerCase()}">${msg.direction}</span>
                    <span class="mqtt-topic">${msg.topic}</span>
                    <span class="mqtt-payload">${msg.payload || '(empty)'}</span>
                </div>
            `).join('');

            container.innerHTML = html;

            if (autoScroll) {
                container.scrollTop = 0;
            }
        }

        async function refreshMessages() {
            try {
                const response = await fetch('/api/messages?limit=50');
                const data = await response.json();
                renderMessages(data.messages);
            } catch (error) {
                console.error('Failed to fetch messages:', error);
            }
        }

        function clearMessages() {
            document.getElementById('mqtt-messages').innerHTML = '<div class="mqtt-empty">Messages cleared. Click a command button...</div>';
        }

        function toggleAutoScroll() {
            autoScroll = !autoScroll;
            const btn = document.getElementById('auto-scroll-btn');
            btn.classList.toggle('active', autoScroll);
        }

        function startMessagePolling() {
            if (messagePolling) return;
            refreshMessages();
            messagePolling = setInterval(refreshMessages, 1000);
        }

        function stopMessagePolling() {
            if (messagePolling) {
                clearInterval(messagePolling);
                messagePolling = null;
            }
        }

        // Start polling when page loads
        startMessagePolling();

        // ==========================================
        // DRAG AND DROP
        // ==========================================
        let draggedElement = null;
        let draggedDeviceName = null;

        // Load saved order from localStorage
        function getSavedOrder(type) {
            const saved = localStorage.getItem(`watchtower_order_${type}`);
            return saved ? JSON.parse(saved) : null;
        }

        // Save order to localStorage
        function saveOrder(type, order) {
            localStorage.setItem(`watchtower_order_${type}`, JSON.stringify(order));
        }

        // Get current order of devices in a grid
        function getCurrentOrder(gridId) {
            const grid = document.getElementById(gridId);
            const cards = grid.querySelectorAll('.device-card-container');
            return Array.from(cards).map(card => card.dataset.device);
        }

        function handleDragStart(e) {
            draggedElement = e.currentTarget;
            draggedDeviceName = e.currentTarget.dataset.device;
            e.currentTarget.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', draggedDeviceName);
        }

        function handleDragEnd(e) {
            e.currentTarget.classList.remove('dragging');
            document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
            draggedElement = null;
            draggedDeviceName = null;
        }

        function handleDragOver(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            const target = e.currentTarget;
            if (target !== draggedElement && target.classList.contains('device-card-container')) {
                target.classList.add('drag-over');
            }
        }

        function handleDragLeave(e) {
            e.currentTarget.classList.remove('drag-over');
        }

        function handleDrop(e) {
            e.preventDefault();
            const target = e.currentTarget;
            target.classList.remove('drag-over');

            if (!draggedElement || target === draggedElement) return;

            // Get the grid container
            const grid = target.parentElement;
            const cards = Array.from(grid.querySelectorAll('.device-card-container'));

            const draggedIndex = cards.indexOf(draggedElement);
            const targetIndex = cards.indexOf(target);

            if (draggedIndex < targetIndex) {
                // Moving down - insert after target
                grid.insertBefore(draggedElement, target.nextSibling);
            } else {
                // Moving up - insert before target
                grid.insertBefore(draggedElement, target);
            }

            // Save the new order
            const gridId = grid.id;
            const type = gridId === 'bac-grid' ? 'bac' : 'esp32';
            const newOrder = getCurrentOrder(gridId);
            saveOrder(type, newOrder);

            console.log(`Saved ${type} order:`, newOrder);
        }

        // Sort devices based on saved order
        function sortDevicesByOrder(devices, type) {
            const savedOrder = getSavedOrder(type);
            if (!savedOrder) return devices;

            const deviceMap = {};
            devices.forEach(d => deviceMap[d.name] = d);

            const sorted = [];
            // First add devices in saved order
            savedOrder.forEach(name => {
                if (deviceMap[name]) {
                    sorted.push({ name, device: deviceMap[name] });
                    delete deviceMap[name];
                }
            });
            // Then add any new devices not in saved order
            Object.entries(deviceMap).forEach(([name, device]) => {
                sorted.push({ name, device });
            });

            return sorted;
        }
    </script>
</body>
</html>
"""


@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route('/api/status')
def api_status():
    if checker:
        return jsonify(checker.get_status_summary())
    return jsonify({"error": "System checker not initialized"}), 500


@app.route('/api/ping/<device_name>')
def api_ping_device(device_name):
    if not checker:
        return jsonify({"error": "System checker not initialized"}), 500
    
    result = checker.ping_device(device_name)
    return jsonify({"device": device_name, "ping_sent": result})


@app.route('/api/ping-all')
def api_ping_all():
    if not checker:
        return jsonify({"error": "System checker not initialized"}), 500

    checker.ping_all_devices()
    return jsonify({"status": "pinging all devices"})


@app.route('/api/command/<device_name>/<command>')
def api_send_command(device_name, command):
    """Send a specific command to a device."""
    if not checker:
        return jsonify({"error": "System checker not initialized"}), 500

    if device_name not in checker.devices:
        return jsonify({"error": f"Unknown device: {device_name}"}), 404

    if not checker.mqtt_connected:
        return jsonify({"error": "MQTT not connected"}), 503

    device = checker.devices[device_name]

    # Build topic based on device type
    if device.device_type == DeviceType.ESP32:
        topic = f"MermaidsTale/{device.topic_base}/command"
    else:
        # BAC controllers use different topic structure
        topic = f"{device.topic_base}/set/{command.lower()}"

    # Send the command
    checker.mqtt_client.publish(topic, command)
    checker.log_message("TX", topic, command, device_name)
    logger.info(f"→ Sent {command} to {device_name} on {topic}")

    return jsonify({
        "device": device_name,
        "command": command,
        "topic": topic,
        "sent": True
    })


@app.route('/api/messages')
def api_get_messages():
    """Get recent MQTT messages."""
    if not checker:
        return jsonify({"error": "System checker not initialized"}), 500

    limit = request.args.get('limit', 50, type=int)
    return jsonify({"messages": checker.get_messages(limit)})


def run_timeout_checker():
    """Background thread to check for ping timeouts."""
    while True:
        if checker:
            checker.check_timeouts()
        time.sleep(0.5)


def main():
    global checker
    
    print("=" * 50)
    print("  Alchemy Escape Room System Checker v2")
    print("=" * 50)
    print()
    
    # Initialize system checker
    checker = SystemChecker()
    
    # Connect to MQTT
    if not checker.connect_mqtt():
        print("WARNING: Could not connect to MQTT broker")
        print(f"         Make sure broker is running at {checker.broker_host}:{checker.broker_port}")
    
    # Start background timeout checker
    timeout_thread = threading.Thread(target=run_timeout_checker, daemon=True)
    timeout_thread.start()
    
    # Start web server
    print()
    print(f"Dashboard: http://localhost:5000")
    print()
    print("Press Ctrl+C to stop")
    print()
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    main()
