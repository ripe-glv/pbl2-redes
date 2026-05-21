window.DISTRIBUTED_CONFIG = {
  brokers: [
    { id: "broker-a", sensorId: "sensor-a-1", name: "Area 1", label: "Broker A", url: "http://172.16.103.8:8001", x: 18, y: 24 },
    { id: "broker-b", sensorId: "sensor-b-1", name: "Area 2", label: "Broker B", url: "http://172.16.103.9:8002", x: 48, y: 18 },
    { id: "broker-c", sensorId: "sensor-c-1", name: "Area 3", label: "Broker C", url: "http://172.16.103.9:8003", x: 76, y: 32 },
    { id: "broker-d", sensorId: "sensor-d-1", name: "Area 4", label: "Broker D", url: "http://172.16.103.10:8004", x: 30, y: 72 },
    { id: "broker-e", sensorId: "sensor-e-1", name: "Area 5", label: "Broker E", url: "http://172.16.103.10:8005", x: 68, y: 76 },
  ],
  drones: [
    { id: "drone-base-1", name: "Base 1", droneName: "Drone 1", url: "http://172.16.103.8:9001", x: 24, y: 48 },
    { id: "drone-base-2", name: "Base 2", droneName: "Drone 2", url: "http://172.16.103.9:9002", x: 52, y: 48 },
    { id: "drone-base-3", name: "Base 3", droneName: "Drone 3", url: "http://172.16.103.10:9003", x: 80, y: 58 },
  ],
};
