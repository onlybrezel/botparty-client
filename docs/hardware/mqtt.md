# MQTT Publish

> **Release status: community.** No current end-to-end HIL evidence exists. Production movement is
> blocked.

The `mqtt_pub` adapter publishes control commands to an MQTT broker. This is useful when:

- Your motor firmware already subscribes to MQTT topics (common in ROS2 / Home Assistant setups)
- You want to decouple the BotParty client from your hardware layer
- You are running BotParty on a different host from the robot's low-level controller

```yaml
hardware:
  type: "mqtt_pub"
  options:
    host: "broker.example.com"
    port: 8883
    topic: "botparty/robot/command"
    stop_topic: "botparty/robot/stop"
    status_topic: "botparty/robot/status"
    payload_mode: "plain"    # or "json"
    stop_command: "stop"
    qos: 1
    tls: true
    ca_file: "/etc/ssl/certs/ca-certificates.crt"
```

## How it works

The adapter waits for a successful broker connection and QoS acknowledgement. A disconnected
broker, publish rejection or acknowledgement timeout is returned as a failed command. Remote
brokers require certificate-verified TLS; publish topics cannot contain wildcards.

MQTT delivery acknowledgement proves broker receipt, not that a motor controller has stopped.
The adapter therefore remains motion-disabled in release metadata until a downstream controller,
independent hardware cutoff and current HIL report prove the complete stop path. It is safe for
non-moving integration tests before that evidence exists.

### Payload modes

**`plain`** (default)

A simple string:

```
forward
left:50
stop
```

**`json`**

```json
{"command": "forward", "value": null}
{"command": "left", "value": 50}
{"command": "stop", "value": null}
```

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `host` | string | `localhost` | MQTT broker hostname or IP |
| `port` | int | `1883` | MQTT broker port |
| `topic` | string | `botparty/robot/command` | Topic to publish commands to |
| `username` | string | `null` | MQTT username (if broker requires auth) |
| `password` | string | `null` | MQTT password |
| `payload_mode` | string | `plain` | `"plain"` or `"json"` |
| `stop_command` | string | `stop` | Command published on emergency stop |
| `stop_topic` | string | command topic | Dedicated stop topic |
| `status_topic` | string | `botparty/robot/status` | Retained online/offline status topic |
| `qos` | int | `1` | Broker acknowledgement level; only 1 or 2 |
| `ack_timeout_sec` | float | `1.0` | Connection and publish acknowledgement deadline |
| `tls` | bool | remote: `true` | TLS may be disabled only for a loopback broker |
| `ca_file` | string | system trust | Optional CA bundle path |

## ROS2 bridge example

If you are using the `ros-mqtt-bridge` package, subscribe to the topic on the ROS2 side:

```yaml
# ros_mqtt_bridge config
bridge:
  - ros_topic: /cmd_vel
    mqtt_topic: botparty/robot/command
    ros_type: std_msgs/String
    direction: mqtt_to_ros
```

## Dependencies

```bash
pip install paho-mqtt
```

To test the broker locally with Mosquitto:

```bash
sudo apt install mosquitto mosquitto-clients
mosquitto_sub -t "botparty/robot/command" -v   # watch incoming commands
```
