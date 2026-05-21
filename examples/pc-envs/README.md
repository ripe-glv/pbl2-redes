# Envs por computador

Use estes arquivos para escolher a topologia. O modo distribuido usa `docker-compose.cluster.yml`; o modo tudo em um PC usa `docker-compose.yml`.

## Tudo em um PC

```bash
docker compose --env-file examples/pc-envs/all-in-one.env up --build
```

Sobe:

- 5 brokers
- 5 sensores
- 3 bases de drone
- 3 drones
- dashboard

Abra:

```text
http://localhost:8080
```

## Distribuido em 3 PCs

Antes de rodar, edite o bloco de IPs no topo dos tres arquivos:

```env
PC1_IP=...
PC2_IP=...
PC3_IP=...
```

Em cada arquivo, trocar um IP nesse bloco atualiza todas as URLs usadas pelos componentes daquele env.

No PC 1:

```bash
docker compose --env-file examples/pc-envs/pc1.env -f docker-compose.cluster.yml up --build
```

No PC 2:

```bash
docker compose --env-file examples/pc-envs/pc2.env -f docker-compose.cluster.yml up --build
```

No PC 3:

```bash
docker compose --env-file examples/pc-envs/pc3.env -f docker-compose.cluster.yml up --build
```

## Distribuicao

- PC 1: `broker-a`, `sensor-a-1`, `drone-base-1`.
- PC 2: `broker-b`, `broker-c`, `sensor-b-1`, `sensor-c-1`, `drone-base-2`.
- PC 3: `broker-d`, `broker-e`, `sensor-d-1`, `sensor-e-1`, `drone-base-3`.

Para parar em cada PC:

```bash
docker compose -f docker-compose.cluster.yml down
```

Para parar o modo tudo em um PC:

```bash
docker compose --env-file examples/pc-envs/all-in-one.env down
```
