# Alchemy Escape Room System Checker

Pre-game diagnostic tool for verifying all BAC controllers and ESP32 microcontrollers are online and responding.

## Quick Start (Windows)

1. Copy this folder to your M3 computer
2. Double-click `run_checker.bat`
3. Open http://localhost:5000 in your browser

## Features

- **Real-time monitoring** of all devices via MQTT
- **Color-coded status**: Green (online), Yellow (stale), Red (offline), Gray (unknown)
- **Connectivity tests**: Verify MQTT round-trip to each device
- **Physical tests**: Trigger brief actuation to confirm hardware chain (for safe devices)
- **Web dashboard**: Access from any device on your network

## Device Status Thresholds

### BAC Controllers
- **Online**: Heartbeat received within 15 seconds
- **Stale**: No heartbeat for 15-30 seconds
- **Offline**: No heartbeat for 30+ seconds

### ESP32 Microcontrollers
- **Online**: Status received within 6 minutes
- **Stale**: No status for 6-10 minutes
- **Offline**: No status for 10+ minutes

## Configuration

Edit `config.json` to add/remove devices or change settings:

```json
{
    "broker_host": "10.1.10.115",
    "broker_port": 1860,
    
    "bac_controllers": [
        {
            "name": "Shattic",
            "safe_to_test": true,
            "description": "Shattic area BAC controller"
        }
    ],
    
    "esp32_devices": [
        {
            "name": "JungleDoor",
            "topic": "JungleDoor",
            "safe_to_test": false,
            "description": "Sliding door - NO PHYSICAL TEST"
        }
    ]
}
```

### Configuration Options

| Field | Description |
|-------|-------------|
| `broker_host` | MQTT broker IP address |
| `broker_port` | MQTT broker port |
| `name` | Display name for the device |
| `topic` | MQTT topic suffix (for ESP32s, prepended with `MermaidsTale/`) |
| `safe_to_test` | If `false`, physical tests are disabled (connectivity only) |
| `description` | Optional description for documentation |

## How Testing Works

### BAC Controllers
- **Connectivity**: Listens for heartbeat messages (`{name}/get/heartbeat`)
- **Physical**: Pulses Output_0 for 100ms (`{name}/set/Output_0`)

### ESP32 Devices
- **Connectivity**: Sends `STATUS` to `MermaidsTale/{topic}/command`
- **Physical**: Same as connectivity (device should respond with current state)

## Adding a STATUS Command to Your ESP32s

If your ESP32 devices don't have a STATUS command, add this to the MQTT callback:

```cpp
void mqtt_callback(char* topic, byte* payload, unsigned int length) {
    String message;
    for (unsigned int i = 0; i < length; i++) {
        message += (char)payload[i];
    }
    
    if (message == "STATUS") {
        // Report current state
        String status = "online";
        mqtt.publish(mqtt_topic_status, status.c_str(), true);
        return;
    }
    
    // ... rest of your command handling
}
```

## Troubleshooting

### "MQTT: Disconnected"
- Check that the MQTT broker is running on 10.1.10.115:1860
- Verify the M3 PC can ping the broker
- Check firewall settings

### Device shows "Unknown"
- Device has never sent a message since the checker started
- Verify the device is powered on and connected to the network
- Check the topic name matches what the device publishes to

### Device shows "Offline" but should be working
- Check physical network connection to the device
- Verify WiFi credentials on ESP32 devices
- Check MQTT broker logs for connection attempts

### Physical test doesn't trigger anything
- Verify `safe_to_test` is `true` in config.json
- For BACs: Check that Output_0 is wired to something observable
- For ESP32s: Verify the device handles the STATUS command

## Requirements

- Python 3.7+
- Windows (for .bat launcher) or any OS (run `python system_checker.py` directly)
- Network access to MQTT broker

## Manual Installation

```bash
pip install paho-mqtt flask
python system_checker.py
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Web dashboard |
| `GET /api/status` | JSON status of all devices |
| `GET /api/test/{name}?physical=true` | Test specific device |
| `GET /api/test-all?physical=true` | Test all devices |

---

Built for Alchemy Escape Rooms Inc.
