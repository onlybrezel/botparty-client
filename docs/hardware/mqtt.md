# MQTT Publish

The `mqtt_pub` adapter publishes control commands to an MQTT broker. This is useful when:

- Your motor firmware already subscribes to MQTT topics (common in ROS2 / Home Assistant setups)
- You want to decouple the BotParty client from your hardware layer
- You are running BotParty on a different host from the robot's low-level controller

```yaml
hardware:
  type: "mqtt_pub"
  options:
    host: "localhost"
    port: 1883
    topic: "botparty/robot/command"
    payload_mode: "plain"    # or "json"
    stop_command: "stop"
```

---

## How it works

The adapter connects to an MQTT broker on startup and publishes each command to the configured topic. If the connection drops it attempts to reconnect automatically before each publish.

### Payload modes

**`plain`** (default)

A simple string:

```
forward
left 50
stop
```

**`json`**

```json
{"command": "forward", "value": null}
{"command": "left", "value": 50}
{"command": "stop", "value": null}
```

---

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

---

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

---

## Dependencies

```bash
pip install paho-mqtt
```

To test the broker locally with Mosquitto:

```bash
sudo apt install mosquitto mosquitto-clients
mosquitto_sub -t "botparty/robot/command" -v   # watch incoming commands
```
