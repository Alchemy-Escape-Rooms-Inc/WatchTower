# Prop Coding Standards for System Checker Compatibility

This document defines the required code patterns for all ESP32-based props to be compatible with the Alchemy System Checker. Follow these standards when creating or updating any prop firmware.

---

## MQTT Topic Structure

All ESP32 props must use this topic structure:

```
Game Name:     MermaidsTale (no spaces, PascalCase)
Prop Name:     The unique identifier for this prop (e.g., Driftwood, StarCharts)

Subscribe to:  MermaidsTale/{PropName}/command
Publish to:    MermaidsTale/{PropName}/command   (for command responses like PONG)
               MermaidsTale/{PropName}/status    (for state updates)
               MermaidsTale/{PropName}/log       (for debug output)
```

### Example for a prop named "Driftwood":
```cpp
#define GAME_NAME "MermaidsTale"
#define PROP_NAME "Driftwood"

#define MQTT_TOPIC_COMMAND "MermaidsTale/Driftwood/command"
#define MQTT_TOPIC_STATUS  "MermaidsTale/Driftwood/status"
#define MQTT_TOPIC_LOG     "MermaidsTale/Driftwood/log"
```

---

## Required Commands

Every prop MUST respond to these commands on the `/command` topic:

| Command | Response | Description |
|---------|----------|-------------|
| `PING` | `PONG` | Health check - System Checker uses this |
| `STATUS` | Current state | Report puzzle state (e.g., "SOLVED", "READY") |
| `RESET` | `OK` | Reboot the device |
| `PUZZLE_RESET` | `OK` | Reset puzzle state without rebooting |

### PING/PONG Implementation (CRITICAL)

The system checker sends `PING` and expects `PONG` back on the same `/command` topic.

```cpp
// In your command handler:
if (strcmp(message, "PING") == 0) {
    mqttClient.publish(MQTT_TOPIC_COMMAND, "PONG");
    return;
}
```

---

## MQTT Callback Stack Corruption Fix (CRITICAL)

The PubSubClient library passes a `char* topic` pointer that can get corrupted by local variables in the callback. **YOU MUST COPY THE TOPIC IMMEDIATELY** at the start of the callback.

### The Problem

```cpp
// BAD - topic pointer gets corrupted by local variables
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    char message[128];  // This corrupts the topic pointer!
    // ... more code ...

    if (strcmp(topic, MQTT_TOPIC_COMMAND) == 0) {  // FAILS - topic is garbage
```

### The Solution

```cpp
// GOOD - copy topic first before any other variables
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    // ============================================================
    // CRITICAL: Copy topic to local buffer FIRST
    // This prevents stack corruption from overwriting the topic pointer
    // ============================================================
    char topicBuf[128];
    strncpy(topicBuf, topic, sizeof(topicBuf) - 1);
    topicBuf[sizeof(topicBuf) - 1] = '\0';

    // Now safe to declare other local variables
    char message[128];
    // ... rest of code ...

    // Use topicBuf instead of topic everywhere
    if (strcmp(topicBuf, MQTT_TOPIC_COMMAND) == 0) {  // WORKS!
```

---

## Complete MQTT Callback Template

Copy this template for every new prop:

```cpp
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    // ============================================================
    // CRITICAL: Copy topic to local buffer FIRST
    // This prevents stack corruption from overwriting the topic pointer
    // ============================================================
    char topicBuf[128];
    strncpy(topicBuf, topic, sizeof(topicBuf) - 1);
    topicBuf[sizeof(topicBuf) - 1] = '\0';

    // Now safe to declare other variables
    char message[128];
    if (length >= sizeof(message)) {
        length = sizeof(message) - 1;
    }
    memcpy(message, payload, length);
    message[length] = '\0';

    // Trim whitespace
    char* msg = message;
    while (*msg == ' ' || *msg == '\t' || *msg == '\r' || *msg == '\n') msg++;
    char* end = msg + strlen(msg) - 1;
    while (end > msg && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n')) {
        *end = '\0';
        end--;
    }

    // Log received command
    Serial.printf("[MQTT] Received on %s: %s\n", topicBuf, msg);

    // Only process commands on our command topic
    if (strcmp(topicBuf, MQTT_TOPIC_COMMAND) != 0) {
        return;
    }

    // ============================================================
    // REQUIRED COMMANDS - All props must implement these
    // ============================================================

    // PING - Health check for System Checker
    if (strcmp(msg, "PING") == 0) {
        mqttClient.publish(MQTT_TOPIC_COMMAND, "PONG");
        Serial.println("[MQTT] PING -> PONG");
        return;
    }

    // STATUS - Report current puzzle state
    if (strcmp(msg, "STATUS") == 0) {
        // Replace with your actual state
        const char* state = puzzleSolved ? "SOLVED" : "READY";
        mqttClient.publish(MQTT_TOPIC_COMMAND, state);
        Serial.printf("[MQTT] STATUS -> %s\n", state);
        return;
    }

    // RESET - Reboot the device
    if (strcmp(msg, "RESET") == 0) {
        mqttClient.publish(MQTT_TOPIC_COMMAND, "OK");
        Serial.println("[MQTT] RESET -> Rebooting...");
        delay(100);
        ESP.restart();
        return;
    }

    // PUZZLE_RESET - Reset puzzle state without rebooting
    if (strcmp(msg, "PUZZLE_RESET") == 0) {
        puzzleSolved = false;
        // Add your puzzle reset logic here
        mqttClient.publish(MQTT_TOPIC_COMMAND, "OK");
        Serial.println("[MQTT] PUZZLE_RESET -> OK");
        return;
    }

    // ============================================================
    // PROP-SPECIFIC COMMANDS - Add custom commands below
    // ============================================================

    // Example: SOLVE - Force solve the puzzle
    if (strcmp(msg, "SOLVE") == 0) {
        puzzleSolved = true;
        mqttClient.publish(MQTT_TOPIC_COMMAND, "SOLVED");
        return;
    }

    // Unknown command
    Serial.printf("[MQTT] Unknown command: %s\n", msg);
}
```

---

## MQTT Helper Function for Logging

Add this helper function to easily publish log messages:

```cpp
void mqttLogf(const char* format, ...) {
    char buffer[256];
    va_list args;
    va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);

    mqttClient.publish(MQTT_TOPIC_LOG, buffer);
    Serial.println(buffer);
}

// Usage:
mqttLogf("Sensor %d triggered with value %d", sensorId, value);
```

---

## MQTT Connection Setup

```cpp
#include <WiFi.h>
#include <PubSubClient.h>

// WiFi credentials
const char* WIFI_SSID = "your_ssid";
const char* WIFI_PASS = "your_password";

// MQTT broker
const char* MQTT_SERVER = "10.1.10.115";
const int MQTT_PORT = 1883;

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

void setupMQTT() {
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);  // Increase if needed
}

void connectMQTT() {
    while (!mqttClient.connected()) {
        Serial.print("Connecting to MQTT...");

        String clientId = PROP_NAME;
        clientId += "_";
        clientId += String(random(0xffff), HEX);

        if (mqttClient.connect(clientId.c_str())) {
            Serial.println("connected!");

            // Subscribe to command topic
            mqttClient.subscribe(MQTT_TOPIC_COMMAND);

            // Announce we're online
            mqttClient.publish(MQTT_TOPIC_STATUS, "ONLINE");
            mqttLogf("%s v%s online", PROP_NAME, VERSION);

        } else {
            Serial.printf("failed (rc=%d), retrying in 5s\n", mqttClient.state());
            delay(5000);
        }
    }
}

void loop() {
    if (!mqttClient.connected()) {
        connectMQTT();
    }
    mqttClient.loop();

    // Your prop logic here...
}
```

---

## Adding a New Prop to System Checker

After creating a new prop, add it to the system checker config:

**File:** `config.json`

```json
{
    "esp32_devices": [
        {
            "name": "YourPropName",
            "topic": "YourPropName",
            "icon": "XX",
            "color": "#4A90D9"
        }
    ]
}
```

- `name`: Display name in the dashboard
- `topic`: Must match PROP_NAME in your firmware (used in MQTT topics)
- `icon`: 1-2 character icon for the dashboard
- `color`: Hex color for the dashboard card

---

## Version Number Standards

Every prop must have a version number. Update it whenever you make changes.

```cpp
#define VERSION "1.0.0"

// Version format: MAJOR.MINOR.PATCH
// MAJOR: Breaking changes or major rewrites
// MINOR: New features or significant changes
// PATCH: Bug fixes and small tweaks
```

Update the version in:
1. The `#define VERSION` line in the .ino file
2. Any version comments in the file header
3. The Prop Code Version List (if maintained)

---

## Testing Checklist

Before deploying a prop, verify:

1. [ ] PING command returns PONG on /command topic
2. [ ] STATUS command returns current state
3. [ ] RESET command reboots the device
4. [ ] PUZZLE_RESET resets without rebooting
5. [ ] Device appears in System Checker dashboard
6. [ ] Device shows as ONLINE after clicking "Test All"
7. [ ] Response time is reasonable (<500ms for ESP32)

### Quick Test with MQTT Explorer

1. Subscribe to `MermaidsTale/{PropName}/#`
2. Publish `PING` to `MermaidsTale/{PropName}/command`
3. Verify `PONG` appears on `MermaidsTale/{PropName}/command`

---

## Troubleshooting

### PING received but no PONG response

1. Check for stack corruption - ensure topic is copied first in callback
2. Add debug logging to see if command is being processed
3. Verify strcmp is checking the right variable (topicBuf, not topic)

### Device shows offline in System Checker

1. Verify topic names match between firmware and config.json
2. Check MQTT broker connection
3. Verify device is subscribed to /command topic
4. Check serial output for errors

### Debug Code

Add this temporarily to diagnose issues:

```cpp
// After copying topic and message:
Serial.printf("DEBUG: topicBuf=[%s]\n", topicBuf);
Serial.printf("DEBUG: msg=[%s] len=%d\n", msg, strlen(msg));
Serial.printf("DEBUG: strcmp(msg, PING)=%d\n", strcmp(msg, "PING"));

// Print hex bytes of message
Serial.print("DEBUG HEX: ");
for (int i = 0; i < strlen(msg); i++) {
    Serial.printf("%02X ", (unsigned char)msg[i]);
}
Serial.println();
```

---

## Summary

**Every ESP32 prop MUST:**

1. Subscribe to `MermaidsTale/{PropName}/command`
2. Copy topic to local buffer FIRST in mqttCallback (prevents stack corruption)
3. Respond to PING with PONG on the /command topic
4. Implement STATUS, RESET, and PUZZLE_RESET commands
5. Be added to config.json with matching topic name

---

*Document created: 2025-12-21*
*For: Alchemy Escape Rooms - A Mermaid's Tale*
