# A2F monitoring — COMMANDCENTER side (mqtt_agent.py)

WatchTower now monitors A2F as a `software_service` (see `config.json` →
`software_services` and the `DeviceType.SOFTWARE` path in `system_checker.py`).

For it to actually go **ONLINE** on the dashboard, the A2F agent on
**COMMANDCENTER** (`mqtt_agent.py`) must do BOTH of these. WatchTower does
case-sensitive topic matching — the capitalized `/Command` and `/Status` are
intentional and must match exactly.

Broker: `10.1.10.115:1883` (same as everything else).

## 1. Answer PING on the Command topic → reply PONG on the Status topic
WatchTower publishes `PING` to `MermaidsTale/A2F/Command` and waits up to 10s
for ANY message on `MermaidsTale/A2F/Status`.

```python
CMD_TOPIC    = "MermaidsTale/A2F/Command"
STATUS_TOPIC = "MermaidsTale/A2F/Status"

def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8", errors="ignore").strip()
    if msg.topic == CMD_TOPIC:
        if payload == "PING":
            # cheapest proof-of-life: confirm A2F/NIM is reachable, then:
            client.publish(STATUS_TOPIC, "PONG", qos=1, retain=False)
        elif payload == "STATUS":
            # report real health if you can (e.g. "healthy" / "serving")
            client.publish(STATUS_TOPIC, "healthy", qos=1, retain=False)
        # RESTART / STOP already handled by existing a2f_remote.py flow

client.subscribe(CMD_TOPIC, qos=1)
```

## 2. Publish a periodic heartbeat (passive monitoring, survives missed pings)
Send every ~5s. WatchTower marks A2F ONLINE on any heartbeat regardless of
ping state, and treats `/heartbeat` as a hidden topic (won't spam the log).

```python
HEARTBEAT_TOPIC = "MermaidsTale/A2F/heartbeat"

# in a background thread / timer loop:
while True:
    client.publish(HEARTBEAT_TOPIC, "alive", qos=0, retain=False)
    time.sleep(5)
```

Optional: include real status in the heartbeat payload (e.g. `"serving"` vs
`"loading"`) — WatchTower currently treats any heartbeat as ONLINE, but the
payload will show in detailed views.

## Verify
1. Restart WatchTower (run_checker.bat) — an **A2F** card appears (icon AF, NVIDIA green).
2. With mqtt_agent running on COMMANDCENTER, the card goes ONLINE within ~5s (heartbeat) or on the next ping.
3. Kill mqtt_agent → card flips OFFLINE after the 10s timeout / missed heartbeats.
4. `python a2f_remote.py status` should still work (unchanged — same Command/Status topics).
