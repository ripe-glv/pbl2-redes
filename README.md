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
- `docker-compose.yml`: sobe a topologia completa em um unico PC.
- `docker-compose.cluster.yml`: sobe a topologia distribuida em 3 PCs usando os envs de `examples/pc-envs`.
- `dashboard/`: interface tatico-operacional com grid de latitude/longitude, areas dos brokers, bases, drones e ocorrencias.
- `tests/test_consistency.py`: teste automatizado de carga concorrente e nao-duplicidade de despacho.

## Funcionamento da aplicacao

1. Cada sensor gera eventos dentro da area quadrada do seu broker no mapa tatico.
2. Eventos com severidade maior ou igual a `ALERT_THRESHOLD` viram requisicoes de drone.
3. O broker de origem coloca a requisicao em sua fila de prioridade e replica uma copia para os demais brokers via `/enqueue-peer`.
4. Todos os brokers podem tentar ajudar no despacho, mas antes precisam obter um claim no broker de origem via `/claim-request`.
5. Com o claim concedido, o broker consulta `/status` de todas as bases em `DRONES`, filtra drones vivos e livres, calcula a distancia ate a latitude/longitude da ocorrencia e tenta reservar o drone disponivel mais proximo.
6. O drone aceito executa a missao em uma thread e chama `/drone-result` no broker de origem ao concluir ou falhar.
7. Se o drone cair ou nao responder, o broker detecta timeout por `MISSION_TIMEOUT_SECONDS`, recoloca a ocorrencia na fila e tenta novamente.

O sistema nao depende de um servidor central unico: cada broker mantem sua propria fila, replica requisicoes entre pares e usa claims no broker de origem para impedir que a mesma ocorrencia seja despachada duas vezes.

## Como a concorrencia e tratada

O projeto usa duas travas distribuidas simples:

1. Trava do drone: cada drone possui estado local protegido por lock. Se dois brokers tentarem reservar o mesmo drone, apenas uma reserva e aceita.
2. Claim da ocorrencia: cada requisicao tem um broker de origem. Antes de qualquer setor despachar um drone para aquela ocorrencia, ele precisa obter um claim do broker de origem. Depois que o claim vira despacho confirmado, outros setores nao conseguem despachar outro drone para a mesma requisicao.

Isso evita:

- o mesmo drone em duas ocorrencias simultaneas;
- dois drones enviados para a mesma ocorrencia;
- dependencia de um coordenador global centralizado.

A fila usa prioridade por criticidade e, em empate, ordem de criacao (`created_at_ms`). Assim, ocorrencias mais criticas saem primeiro e eventos de mesma criticidade preservam ordering por chegada.

## Escolha do drone

Antes de reservar uma missao, o broker consulta `/status` de todas as bases em `DRONES`. Ele considera disponivel somente o drone vivo, livre (`idle`) e com base operacional ou base de comando alternativa. Entre os drones disponiveis, o broker converte a latitude/longitude da ocorrencia para o mapa operacional e escolhe o drone mais proximo pela distancia euclidiana no grid tatico.

Se dois brokers tentarem escolher o mesmo drone ao mesmo tempo, a reserva final ainda passa pelo lock local do drone em `/reserve`; se ele ja ficou ocupado, o broker tenta o proximo candidato disponivel por distancia.

## Confiabilidade e replanejamento

O broker trabalha com timeouts e ACKs simples:

- chamadas HTTP entre componentes usam timeout curto;
- respostas HTTP 2xx funcionam como ACK de enfileiramento, claim, reserva e resultado;
- quando uma reserva falha ou todos os drones estao ocupados, o claim e liberado e a requisicao volta para a fila;
- quando um drone conclui ou falha a missao, ele chama `/drone-result`;
- se o drone cair depois da reserva e nao enviar callback, `MISSION_TIMEOUT_SECONDS` expira e o broker recoloca a requisicao na fila;
- cada despacho recebe um numero de `attempt`, entao callbacks atrasados de uma tentativa antiga sao rejeitados e nao sobrescrevem uma tentativa mais nova.

Esse mecanismo cobre o caso de drone abatido, desconectado ou lento demais para responder.

## Conexoes e desconexoes

A aplicacao nao usa conexoes persistentes, sockets abertos ou sessao fixa entre os modulos. Toda comunicacao acontece por HTTP curto, do tipo requisicao/resposta. Por isso, quando algum modulo cai ou a rede falha, nao existe uma etapa explicita de "reconectar"; o sistema simplesmente tenta chamar o endpoint de novo em um ciclo futuro.

As chamadas HTTP usam timeout curto no helper `http_json`. Quando a conexao falha, expira ou retorna erro, o chamador recebe status `0` ou um codigo HTTP de erro e decide o proximo passo.

### Sensor para broker

O sensor roda em loop:

1. gera um evento;
2. envia `POST /sensor` para `BROKER_URL`;
3. aguarda a resposta;
4. dorme `INTERVAL_SECONDS`;
5. repete.

Se o broker estiver fora do ar ou inacessivel, a chamada falha por timeout/conexao. O evento daquela rodada nao e armazenado em buffer pelo sensor, mas o sensor continua vivo e tenta enviar um novo evento no proximo ciclo. Quando o broker voltar, as proximas chamadas voltam a ser aceitas automaticamente.

### Broker para broker

Os brokers se comunicam pelos enderecos em `PEERS`.

Quando uma ocorrencia critica nasce em um broker, ele envia copias para os pares com `POST /enqueue-peer`. Cada par que responder com HTTP 2xx recebeu a copia. Se algum peer estiver fora, apenas aquela chamada falha; os demais brokers continuam operando.

Para evitar duplicidade, uma ocorrencia sempre tem um broker de origem (`origin_broker` e `origin_url`). Antes de qualquer broker despachar um drone para essa ocorrencia, ele precisa pedir permissao ao broker de origem com `POST /claim-request`. O broker de origem concede apenas um claim valido por vez. Se o broker ajudante nao conseguir reservar nenhum drone, ele chama `POST /release-claim` para devolver a ocorrencia a fila.

Limite atual: se um peer estiver desconectado no momento da replicacao por `/enqueue-peer`, essa copia nao fica guardada para entrega posterior. O broker de origem continua dono da ocorrencia, mas aquele peer so recebera novas ocorrencias futuras.

### Broker para drones

O broker nao assume que conhece o estado real dos drones pela configuracao. Antes de despachar, ele consulta todos os enderecos em `DRONES` com `GET /status`.

Um drone so entra na lista de candidatos se:

- respondeu com sucesso;
- `drone_alive` esta verdadeiro;
- `status` e `idle`;
- a base esta viva ou o drone possui uma base de comando alternativa.

Depois disso, o broker calcula a distancia entre a latitude/longitude da ocorrencia e a posicao de cada drone no mapa tatico. A reserva e tentada primeiro no drone disponivel mais proximo, usando `POST /reserve`.

Se um drone estiver desconectado, lento, destruido ou ocupado, ele e ignorado naquela rodada. Se nenhum drone puder ser reservado, o claim e liberado e a ocorrencia volta para a fila. Apos `RETRY_SECONDS`, o broker tenta novamente, refazendo as consultas de status. Assim, um drone que volta para a rede volta a ser considerado automaticamente.

### Drone para broker

Quando um drone aceita uma reserva, ele executa a missao em uma thread local. Ao final, envia o resultado para o callback recebido na reserva:

```text
POST {callback_url}
```

Na pratica, esse callback aponta para `/drone-result` no broker de origem da ocorrencia.

Se o drone nao conseguir chamar o broker de origem, o callback pode se perder. Para cobrir esse caso, o broker de origem guarda `mission_deadline_ms`. Se o prazo definido por `MISSION_TIMEOUT_SECONDS` passar sem resultado, o broker marca a tentativa como `timeout`, remove a atribuicao do drone e recoloca a ocorrencia na fila.

Cada reserva tambem possui um numero `attempt`. Se um callback antigo chegar depois de uma nova tentativa ja ter sido criada, o broker rejeita o resultado como `stale_attempt`. Isso impede que uma resposta atrasada sobrescreva o estado mais novo da ocorrencia.

### Dashboard para modulos

O dashboard nao participa do consenso nem do despacho. Ele apenas consulta os modulos periodicamente:

- brokers: `GET /state`;
- drones: `GET /status`;
- controles: `POST /control`;
- ocorrencia manual: `POST /request-drone`.

Se algum broker ou drone nao responder, o dashboard mostra o modulo como offline, mas isso nao para o restante do sistema.

### Resumo por falha

| Falha | Efeito imediato | Recuperacao |
| --- | --- | --- |
| Sensor desconectado | novos eventos daquela area param de chegar | quando volta, o loop envia novos eventos |
| Broker desconectado | sua area para de aceitar sensor/claims/callbacks | quando volta, volta a responder HTTP |
| Peer broker fora | replicacao/claim para ele falha | proximas chamadas tentam novamente |
| Drone ou base fora | broker ignora esse drone na escolha | quando `/status` voltar, ele entra de novo na lista |
| Drone cai durante missao | resultado nao chega | broker replaneja apos `MISSION_TIMEOUT_SECONDS` |
| Dashboard fora | apenas a visualizacao para | sistema continua operando |

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

## Rodando em multiplos computadores

A topologia distribuida pronta fica em `docker-compose.cluster.yml` e nos arquivos `examples/pc-envs`.

Antes de rodar em PCs diferentes, edite os IPs no topo de `examples/pc-envs/pc1.env`, `examples/pc-envs/pc2.env` e `examples/pc-envs/pc3.env`:

```env
PC1_IP=...
PC2_IP=...
PC3_IP=...
```

Trocar um IP uma vez atualiza todas as URLs de peers, drones e callbacks daquele arquivo.

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

Distribuicao:

- PC 1: `broker-a`, `sensor-a-1`, `drone-base-1`.
- PC 2: `broker-b`, `broker-c`, `sensor-b-1`, `sensor-c-1`, `drone-base-2`.
- PC 3: `broker-d`, `broker-e`, `sensor-d-1`, `sensor-e-1`, `drone-base-3`.

As portas `8001` a `8005` e `9001` a `9003` precisam estar liberadas no firewall entre os computadores.

## Interface grafica

Depois de subir o Compose, abra:

```text
http://localhost:8080
```

A tela mostra as cinco areas/brokers, as tres bases de drones, fila de ocorrencias, missoes concluidas e permite criar uma ocorrencia critica manualmente por broker. Ela atualiza automaticamente a cada 3 segundos.

Ela tambem inclui um mapa tatico com grade de latitude/longitude, quadrados de area para cada broker, bases, drones em movimento e ocorrencias. Os controles permitem destruir ou criar novamente qualquer elemento da simulacao. Quando uma base e destruida, o drone que continuou vivo passa a receber comandos da base viva mais proxima; quando a base original volta, o drone volta a obedecer sua propria base.

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
- varias requisicoes concorrentes terminam sem reservar um drone para duas missoes ao mesmo tempo;
- a escolha do drone prioriza o drone disponivel mais proximo da ocorrencia.

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
- `AREA_X`, `AREA_Y`: posicao do centro da area no mapa tatico.
- `AREA_SIZE`: tamanho do quadrado da area no mapa.
- `ALERT_THRESHOLD`: severidade minima para gerar requisicao automatica.
- `CLAIM_TTL_SECONDS`: tempo de validade de um claim.
- `RETRY_SECONDS`: intervalo de nova tentativa quando nao ha drone disponivel.
- `MISSION_TIMEOUT_SECONDS`: tempo maximo para o callback de uma missao antes de replanejar.

Drone:

- `DRONE_ID`: identificador do drone.
- `BASE_X`, `BASE_Y`: posicao da base no mapa tatico.
- `FAIL_RATE`: probabilidade de falha de missao.
- `MIN_MISSION_SECONDS`: duracao minima de missao.
- `MAX_MISSION_SECONDS`: duracao maxima de missao.

Sensor:

- `SENSOR_ID`: identificador do sensor.
- `SECTOR_ID`: setor do sensor.
- `BROKER_URL`: broker para onde os eventos serao enviados.
- `AREA_X`, `AREA_Y`, `AREA_SIZE`: area onde o sensor pode gerar eventos.
- `INTERVAL_SECONDS`: intervalo de geracao de eventos.

## Estrutura

```text
.
|-- Dockerfile
|-- dashboard
|   |-- app.js
|   |-- config.js
|   |-- index.html
|   `-- styles.css
|-- docker-compose.cluster.yml
|-- docker-compose.yml
|-- examples
|   `-- pc-envs
|-- README.md
`-- src
    |-- broker.py
    |-- common.py
    |-- drone.py
    `-- sensor.py
```
