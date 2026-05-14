# PBL 2 - Redes: despacho distribuido de drones

Prototipo de infraestrutura distribuida para coordenacao de drones autonomos em setores maritimos. A solucao simula brokers de setores, sensores autonomos e drones compartilhados, todos isolados em containers Docker.

O foco do projeto e resolver as falhas de concorrencia descritas no enunciado:

- priorizar requisicoes por criticidade e ordem de chegada;
- impedir que um drone aceite duas missoes ao mesmo tempo;
- impedir que a mesma ocorrencia receba mais de um drone;
- manter fila distribuida e replanejar quando uma missao falha.

## Arquitetura

Cada setor maritimo e representado por um broker independente. Nao existe um servidor central unico.

```text
sensor-a -> broker-a ----\
sensor-b -> broker-b -----+---- drones compartilhados
sensor-c -> broker-c ----/
```

Componentes:

- `broker.py`: servidor do setor. Mantem tabela de sensores, fila local de requisicoes e coordena claims de ocorrencias.
- `sensor.py`: sensor autonomo. Gera eventos aleatorios e envia ao broker do seu setor.
- `drone.py`: drone autonomo. Aceita somente uma reserva por vez e executa a missao em uma thread.
- `docker-compose.yml`: sobe tres setores, tres drones e tres sensores.

## Como a concorrencia e tratada

O projeto usa duas travas distribuidas simples:

1. Trava do drone: cada drone possui estado local protegido por lock. Se dois brokers tentarem reservar o mesmo drone, apenas uma reserva e aceita.
2. Claim da ocorrencia: cada requisicao tem um broker de origem. Antes de qualquer setor despachar um drone para aquela ocorrencia, ele precisa obter um claim do broker de origem. Depois que o claim vira despacho confirmado, outros setores nao conseguem despachar outro drone para a mesma requisicao.

Isso evita:

- o mesmo drone em duas ocorrencias simultaneas;
- dois drones enviados para a mesma ocorrencia;
- dependencia de um coordenador global centralizado.

## Requisitos

- Docker
- Docker Compose

Nao ha dependencias Python externas. Os servicos usam apenas a biblioteca padrao do Python.

## Rodando

Na raiz do repositorio:

```bash
docker compose up --build
```

Servicos expostos na maquina:

- Broker A: `http://localhost:8001`
- Broker B: `http://localhost:8002`
- Broker C: `http://localhost:8003`
- Drone 1: `http://localhost:9001`
- Drone 2: `http://localhost:9002`
- Drone 3: `http://localhost:9003`
- Interface grafica: `http://localhost:8080`

## Interface grafica

Depois de subir o Compose, abra:

```text
http://localhost:8080
```

A tela mostra brokers, drones, fila de ocorrencias, missoes concluidas e permite criar uma ocorrencia critica manualmente por broker. Ela atualiza automaticamente a cada 3 segundos.

## Testes manuais

Ver estado de um setor:

```bash
curl http://localhost:8001/state
```

Criar uma ocorrencia critica manualmente:

```bash
curl -X POST http://localhost:8001/request-drone \
  -H "Content-Type: application/json" \
  -d '{"event_type":"unknown_object","criticality":10,"lat":26.4,"lon":56.2}'
```

Ver estado de um drone:

```bash
curl http://localhost:9001/status
```

Simular falha de um setor:

```bash
docker compose stop broker-a
```

Os demais brokers continuam operando e despachando drones para suas proprias ocorrencias. Como o broker de origem e o dono do claim de uma requisicao, requisicoes originadas no setor parado ficam indisponiveis ate ele voltar, mas o restante da operacao continua sem ponto unico de falha global.

## Endpoints principais

### Broker

- `GET /health`: saude do broker.
- `GET /state`: estado interno do setor, sensores, fila e requisicoes.
- `POST /sensor`: recebe evento de sensor. Eventos com severidade alta entram na fila.
- `POST /request-drone`: cria requisicao manual de drone.
- `POST /enqueue-peer`: recebe copia distribuida de requisicao de outro broker.
- `POST /claim-request`: concede claim temporario para uma ocorrencia.
- `POST /release-claim`: libera claim quando nenhum drone pode ser reservado.
- `POST /assignment`: confirma o drone atribuido.
- `POST /drone-result`: recebe conclusao ou falha da missao.

### Drone

- `GET /health`: saude do drone.
- `GET /status`: estado do drone.
- `POST /reserve`: tenta reservar o drone para uma missao.

## Ajustes por variaveis de ambiente

Broker:

- `BROKER_ID`: identificador do broker.
- `SECTOR_ID`: identificador do setor maritimo.
- `PUBLIC_URL`: URL usada pelos pares e drones para callbacks.
- `PEERS`: lista de brokers pares separada por virgula.
- `DRONES`: lista de drones conhecidos separada por virgula.
- `ALERT_THRESHOLD`: severidade minima para gerar requisicao automatica.
- `CLAIM_TTL_SECONDS`: tempo de validade de um claim.
- `RETRY_SECONDS`: intervalo de nova tentativa quando nao ha drone disponivel.

Drone:

- `DRONE_ID`: identificador do drone.
- `FAIL_RATE`: probabilidade de falha de missao.
- `MIN_MISSION_SECONDS`: duracao minima de missao.
- `MAX_MISSION_SECONDS`: duracao maxima de missao.

Sensor:

- `SENSOR_ID`: identificador do sensor.
- `SECTOR_ID`: setor do sensor.
- `BROKER_URL`: broker para onde os eventos serao enviados.
- `INTERVAL_SECONDS`: intervalo de geracao de eventos.

## Estrutura

```text
.
|-- Dockerfile
|-- dashboard
|   |-- app.js
|   |-- index.html
|   `-- styles.css
|-- docker-compose.yml
|-- README.md
`-- src
    |-- broker.py
    |-- common.py
    |-- drone.py
    `-- sensor.py
```
