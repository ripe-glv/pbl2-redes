# PBL 2 - Redes: despacho distribuido de drones

Prototipo de infraestrutura distribuida para coordenacao de drones autonomos em setores maritimos. A solucao simula brokers de setores, sensores autonomos e drones compartilhados, todos isolados em containers Docker.

O foco do projeto e resolver as falhas de concorrencia descritas no enunciado:

- priorizar requisicoes por criticidade e ordem de chegada;
- impedir que um drone aceite duas missoes ao mesmo tempo;
- impedir que a mesma ocorrencia receba mais de um drone;
- manter fila distribuida e replanejar quando uma missao falha.

## Arquitetura

Cada area maritima e representada por um broker independente e por um sensor local. Nao existe um servidor central unico. O estilo arquitetural usado e um conjunto de brokers distribuidos, com comunicacao P2P entre areas para replicar requisicoes e negociar o claim de uma ocorrencia. As tres bases de drones sao compartilhadas por todas as areas, com um drone por base.

```text
sensor-a -> broker-a ----\
sensor-b -> broker-b -----\
sensor-c -> broker-c ------+---- drone-base-1
sensor-d -> broker-d -----+----- drone-base-2
sensor-e -> broker-e ----/------ drone-base-3
```

Componentes:

- `broker.py`: servidor da area. Mantem tabela de sensores, fila local de requisicoes e coordena claims de ocorrencias.
- `sensor.py`: sensor autonomo. Gera eventos aleatorios e envia ao broker da sua area.
- `drone.py`: drone autonomo. Aceita somente uma reserva por vez e executa a missao em uma thread.
- `docker-compose.yml`: sobe cinco areas, cinco brokers, cinco sensores e tres bases de drones.
- `tests/test_consistency.py`: teste automatizado de carga concorrente e nao-duplicidade de despacho.

## Como a concorrencia e tratada

O projeto usa duas travas distribuidas simples:

1. Trava do drone: cada drone possui estado local protegido por lock. Se dois brokers tentarem reservar o mesmo drone, apenas uma reserva e aceita.
2. Claim da ocorrencia: cada requisicao tem um broker de origem. Antes de qualquer setor despachar um drone para aquela ocorrencia, ele precisa obter um claim do broker de origem. Depois que o claim vira despacho confirmado, outros setores nao conseguem despachar outro drone para a mesma requisicao.

Isso evita:

- o mesmo drone em duas ocorrencias simultaneas;
- dois drones enviados para a mesma ocorrencia;
- dependencia de um coordenador global centralizado.

A fila usa prioridade por criticidade e, em empate, ordem de criacao (`created_at_ms`). Assim, ocorrencias mais criticas saem primeiro e eventos de mesma criticidade preservam ordering por chegada.

## Confiabilidade e replanejamento

O broker trabalha com timeouts e ACKs simples:

- chamadas HTTP entre componentes usam timeout curto;
- respostas HTTP 2xx funcionam como ACK de enfileiramento, claim, reserva e resultado;
- quando uma reserva falha ou todos os drones estao ocupados, o claim e liberado e a requisicao volta para a fila;
- quando um drone conclui ou falha a missao, ele chama `/drone-result`;
- se o drone cair depois da reserva e nao enviar callback, `MISSION_TIMEOUT_SECONDS` expira e o broker recoloca a requisicao na fila;
- cada despacho recebe um numero de `attempt`, entao callbacks atrasados de uma tentativa antiga sao rejeitados e nao sobrescrevem uma tentativa mais nova.

Esse mecanismo cobre o caso de drone abatido, desconectado ou lento demais para responder.

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
- Broker D: `http://localhost:8004`
- Broker E: `http://localhost:8005`
- Base de drone 1: `http://localhost:9001`
- Base de drone 2: `http://localhost:9002`
- Base de drone 3: `http://localhost:9003`
- Interface grafica: `http://localhost:8080`

## Interface grafica

Depois de subir o Compose, abra:

```text
http://localhost:8080
```

A tela mostra as cinco areas/brokers, as tres bases de drones, fila de ocorrencias, missoes concluidas e permite criar uma ocorrencia critica manualmente por broker. Ela atualiza automaticamente a cada 3 segundos.

Ela tambem inclui um mapa operacional com areas, brokers, sensores, bases e drones. Os controles permitem destruir ou criar novamente qualquer elemento da simulacao. Quando uma base e destruida, o drone que continuou vivo passa a receber comandos da base viva mais proxima; quando a base original volta, o drone volta a obedecer sua propria base.

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

Simular falha de drone:

```bash
docker compose stop drone-base-1
```

As novas reservas ignoram o drone parado porque `/reserve` falha por timeout/conexao. Se o drone cair durante uma missao ja reservada, o broker aguarda `MISSION_TIMEOUT_SECONDS`, marca o resultado como `timeout` e replaneja automaticamente a ocorrencia.

## Teste automatizado de consistencia

O teste de carga usa somente a biblioteca padrao do Python. Ele sobe cinco brokers e tres bases de drones em portas locais, cria requisicoes concorrentes e valida:

- uma mesma ocorrencia replicada entre setores e despachada apenas uma vez;
- varias requisicoes concorrentes terminam sem reservar um drone para duas missoes ao mesmo tempo.

No Windows desta maquina, use o Python embarcado do Codex:

```bash
C:\Users\lipec\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest tests.test_consistency
```

Em ambientes com `python` no PATH:

```bash
python -m unittest tests.test_consistency
```

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
- `POST /control`: simula destruicao/criacao do broker ou do sensor da area.

Parametros principais:

- `request_id`: identificador unico da ocorrencia.
- `origin_broker` e `origin_url`: broker dono do claim da ocorrencia.
- `criticality`: prioridade da requisicao.
- `claim_token`: token temporario que autoriza o despacho.
- `assigned_drone`: drone escolhido.
- `attempt`: numero da tentativa de despacho.

### Drone

- `GET /health`: saude do drone.
- `GET /status`: estado do drone.
- `POST /reserve`: tenta reservar o drone para uma missao.
- `POST /control`: simula destruicao/criacao da base ou do drone.

Retornos principais:

- `202 {"accepted": true}`: drone reservado e missao iniciada.
- `200 {"accepted": true, "idempotent": true}`: repeticao da mesma reserva/tentativa.
- `409 {"accepted": false, "reason": "busy"}`: drone ja esta em outra missao.

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
- `MISSION_TIMEOUT_SECONDS`: tempo maximo para o callback de uma missao antes de replanejar.

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
