<div align="center">

# asaas-emulator

**Teste sua integração com o Asaas sem internet.**<br>
Pix, boleto, saldo, repasses e webhooks de verdade, num servidor local que se comporta como a API real.

[![CI](https://github.com/SpackiGabriel/asaas-emulator/actions/workflows/ci.yml/badge.svg)](https://github.com/SpackiGabriel/asaas-emulator/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[English](README.md) · **Português**

</div>

```bash
docker build -t asaas-emulator https://github.com/SpackiGabriel/asaas-emulator.git
docker run --rm -p 8000:8000 asaas-emulator
```

Aponte sua integração para `http://localhost:8000` em vez de `https://api-sandbox.asaas.com`. É só isso: sem conta, sem rede, em qualquer linguagem.

---

## O problema

Os bugs que doem numa integração de pagamentos em produção raramente estão no caminho feliz. Estão no webhook que chega duas vezes, no endpoint que ficou fora do ar por uma hora enquanto os eventos se acumulavam, na cobrança que venceu no fim de semana e nos dois repasses que disputaram o mesmo saldo.

São justamente os casos que hoje **não dá** para testar direito:

|  | Sandbox real | Mock feito à mão | **asaas-emulator** |
| --- | :---: | :---: | :---: |
| Funciona offline, sem conta | ✗ | ✓ | ✓ |
| Estado limpo a cada teste | ✗ (compartilhado, nunca reseta) | ✓ | ✓ |
| "Agora são três dias depois" | ✗ | depende | ✓ |
| Seu endpoint de webhook caiu: retentativas, ordem, fila interrompida | ✗ | raramente | ✓ |
| Saldo de verdade: entrada, tarifa, saída, saldo insuficiente | ✓ | raramente | ✓ |
| Ids e respostas determinísticos | ✗ | ✓ | ✓ |
| Continua fiel à API real com o tempo | ✓ | ✗ (diverge em silêncio) | ✓ (suíte de testes de contrato) |
| Rápido o bastante para todo teste | ✗ | ✓ | ✓ |

Um mock devolve o que o autor lembrou. Este emulador guarda estado de verdade, movimenta dinheiro (fictício) de verdade, entrega webhooks por HTTP de verdade e é conferido contra o sandbox real por uma suíte de testes de contrato. **Um fake que mente é pior que nenhum.**

## Como ele se encaixa

```mermaid
flowchart LR
    subgraph seu [Seu projeto]
        app[Sua aplicação]
        hook[Seu endpoint de webhook]
        tests[Sua suíte de testes]
    end
    emu[(asaas-emulator)]
    sandbox[Sandbox do Asaas]

    app -- "API /v3, mesmos caminhos e payloads" --> emu
    emu -- "POST do evento + asaas-access-token" --> hook
    tests -- "/_emulator: reset, mover o tempo, liquidar, inspecionar" --> emu
    emu -. "testes de contrato mantêm a fidelidade" .-> sandbox
```

## Um tour de 30 segundos

```bash
H='access_token: $aact_local'; API=http://localhost:8000

# Um cliente e uma cobrança Pix
curl -s -X POST $API/v3/customers -H "$H" -d '{"name": "Maria Souza", "cpfCnpj": "52998224725"}'
curl -s -X POST $API/v3/payments  -H "$H" \
  -d '{"customer": "cus_000000000001", "billingType": "PIX", "value": 101.99, "dueDate": "2030-01-10"}'

# O cliente paga: a mesma ação de sandbox que o Asaas oferece
curl -s -X POST $API/v3/sandbox/payment/pay_000000000001/confirm -H "$H"
curl -s $API/v3/finance/balance -H "$H"     # {"balance": 100.0}   (tarifa de 1,99 descontada)

# Repasse via Pix
curl -s -X POST $API/v3/transfers -H "$H" \
  -d '{"value": 60, "pixAddressKey": "owner@example.com", "pixAddressKeyType": "EMAIL"}'
curl -s $API/v3/finance/balance -H "$H"     # {"balance": 40.0}
```

Os ids são sequenciais (`cus_000000000001`, `pay_000000000001`, ...), então o mesmo teste contra um emulador novo sempre produz o mesmo tráfego.

## Receitas para os bugs que importam

**Seu receptor precisa aguentar entregas duplicadas.** A entrega é "pelo menos uma vez", então um evento reenviado mantém o mesmo `id`. Faça seu endpoint falhar uma vez e confirme que o pagamento foi processado uma vez só:

```python
# seu endpoint responde 500 na primeira chamada e 200 depois
asaas.post("/_emulator/webhooks/deliver")   # tentativa 1 falha
asaas.post("/_emulator/webhooks/deliver")   # tentativa 2, mesmo id de evento
assert pagamentos_processados == 1
```

**Seu endpoint ficou fora do ar.** Depois de 15 falhas seguidas o webhook fica `interrupted` e a fila para, como diz a documentação. Os eventos continuam se acumulando. Volte `interrupted` para `false` e eles chegam em ordem:

```bash
curl -X PUT $API/v3/webhooks/$WEBHOOK_ID -H "$H" -d '{"interrupted": false}'
```

**Cobranças vencem quando ninguém está olhando.**

```bash
curl -X POST $API/_emulator/clock -d '{"advanceDays": 3}'
# cobranças pendentes com vencimento passado agora estão OVERDUE, e PAYMENT_OVERDUE foi emitido
```

**Repasses sem saldo, ou disputando saldo.** O saldo começa em zero e o débito é atômico. Oito transferências simultâneas contra um saldo que cobre três: exatamente três passam. Tem um teste para isso.

**Um repasse falha depois de aceito.** Rode com `ASAAS_EMULATOR_TRANSFER_MODE=manual` e decida o desfecho:

```bash
curl -X POST $API/_emulator/transfers/$TRANSFER_ID/fail -H "$H" -d '{"failReason": "Chave Pix inexistente"}'
# status FAILED, dinheiro devolvido ao saldo, TRANSFER_FAILED emitido
```

## Use em qualquer linguagem

É um servidor HTTP: o que chama a API real chama o emulador. Exemplos executáveis:

- **Python / pytest**: [`examples/python-pytest`](examples/python-pytest/test_billing_flow.py), com uma fixture que reseta e congela o emulador antes de cada teste.
- **Node.js**: [`examples/node/payout.mjs`](examples/node/payout.mjs), só com o `fetch` nativo.
- **Docker Compose**: [`examples/docker-compose.yml`](examples/docker-compose.yml), para rodar sua aplicação ao lado dele.

No GitHub Actions:

```yaml
- run: |
    pip install git+https://github.com/SpackiGabriel/asaas-emulator.git
    asaas-emulator --port 8000 &
    until curl -fs localhost:8000/_emulator/health; do sleep 0.5; done
- run: pytest
  env:
    ASAAS_BASE_URL: http://localhost:8000
```

## O que ele emula

| Área | Endpoints |
| --- | --- |
| Clientes | `POST/GET /v3/customers`, `GET/PUT/DELETE /v3/customers/{id}`, `POST .../restore` |
| Cobranças | `POST/GET /v3/payments`, `GET/PUT/DELETE /v3/payments/{id}`, `POST .../restore`, `GET .../status` |
| Pix e boleto | `GET .../pixQrCode`, `GET .../identificationField` |
| Dinheiro | `POST .../receiveInCash`, `POST .../undoReceivedInCash` |
| Ações de sandbox | `POST /v3/sandbox/payment/{id}/confirm`, `POST /v3/sandbox/payment/{id}/overdue` |
| Saldo | `GET /v3/finance/balance` |
| Transferências | `POST/GET /v3/transfers`, `GET /v3/transfers/{id}`, `DELETE /v3/transfers/{id}/cancel` |
| Webhooks | `POST/GET /v3/webhooks`, `GET/PUT/DELETE /v3/webhooks/{id}`, e a entrega de fato |

As respostas seguem a referência OpenAPI oficial: o envelope de erro `{"errors": [{"code", "description"}]}`, o envelope de lista `{"object": "list", "hasMore", "totalCount", ...}`, um `404` sem corpo para ids desconhecidos e autenticação pelo header `access_token`.

O que o seu código interpreta tem estrutura real, não placeholder:

- **Payload Pix** é um BR Code (EMV) válido com CRC16 correto, e `encodedImage` é um QR code que dá para escanear.
- **Linha digitável e código de barras** seguem o layout FEBRABAN (47 e 44 dígitos), com dígitos verificadores válidos e o fator de vencimento atual.
- **CPF e CNPJ** são validados pelo dígito verificador, como o sandbox faz.

## Como ele se mantém fiel

Todo comportamento está num de dois grupos, e o README diz qual.

**Tirado da documentação oficial:** caminhos, campos de requisição e resposta, enums de status e eventos, envelopes de erro e lista, os headers `access_token` e `asaas-access-token`, o payload do webhook e o formato do id de evento, entrega "pelo menos uma vez", interrupção após 15 falhas seguidas e as ações de sandbox de confirmar e vencer.

**Suposto, até a suíte de contrato confirmar contra o sandbox:**

- quais eventos a confirmação de sandbox emite (o emulador emite só `PAYMENT_RECEIVED`);
- os códigos exatos de erro de validação e de saldo insuficiente;
- as tarifas padrão e o formato do id de transferência;
- se forçar o vencimento altera o `dueDate` (o emulador mantém).

A suíte de contrato em [`tests/conformance`](tests/conformance/test_contract.py) roda contra o emulador por padrão e contra o sandbox real com uma chave:

```bash
ASAAS_CONFORMANCE_BASE_URL=https://api-sandbox.asaas.com \
ASAAS_CONFORMANCE_API_KEY='$aact_...' \
pytest -m conformance -v
```

Quando os dois discordam, quem muda é o emulador. Achou uma diferença? [Abra um relato de fidelidade](https://github.com/SpackiGabriel/asaas-emulator/issues/new?template=fidelity.yml).

## Decisões de design

- **Cada chave de API é uma conta.** Clientes, cobranças, saldo e webhooks nunca se misturam entre chaves, então testes em paralelo, ou todos os tenants de um produto multi-tenant, podem dividir um emulador.
- **O saldo começa em zero.** Um saldo inicial generoso esconde a falha real mais comum. Abasteça a conta como no sandbox, confirmando uma cobrança, ou defina com `PUT /_emulator/balance`.
- **O débito é um único `UPDATE` condicional.** "Ler o saldo, comparar, escrever" é uma condição de corrida; `UPDATE ... WHERE balance >= valor` não é. Dinheiro não é gasto duas vezes, nem com requisições simultâneas.
- **Mudança de estado e evento entram juntos.** Uma cobrança nunca muda de status sem o evento de webhook entrar na fila na mesma transação (um outbox transacional).
- **O estado vive no SQLite, não em dicionários.** Os handlers e o worker de webhooks compartilham o estado com segurança, e apontar `ASAAS_EMULATOR_DATABASE` para um arquivo mantém tudo entre reinícios sem reemitir ids que já existem.
- **O tempo é uma entrada.** Um relógio controlável transforma comportamento que depende de data em algo testável, não instável.

## Por que isto existe

Este projeto nasceu do [Soleira](https://soleira.app), um SaaS que construí para automatizar o fechamento financeiro mensal de imobiliárias em cima do Asaas: cobrar inquilinos, conciliar pagamentos e repassar aos proprietários via Pix.

Testar esse fluxo de dinheiro contra o sandbox o dia inteiro era lento e instável, então o Soleira tinha o próprio fake do Asaas. A primeira versão era um mock simples, e esse mock escondia bugs reais:

- dava a toda conta um saldo inicial generoso, enquanto uma conta nova do sandbox responde `{"balance": 0}`, o caso que quebra primeiro em produção;
- debitava o saldo com uma leitura seguida de uma escrita, uma condição de corrida esperando acontecer;
- guardava o estado em memória, então reiniciar reemitia ids que já existiam no banco.

Corrigir isso ensinou os princípios acima. O `asaas-emulator` é uma reescrita do zero dessas ideias, como ferramenta independente, para que qualquer time que integra com o Asaas, em qualquer linguagem, possa testar os casos difíceis sem construir o próprio fake.

## Configuração, API de controle e roadmap

As tabelas completas de variáveis de ambiente, endpoints de controle e roadmap estão no [README em inglês](README.md#configuration); os nomes são os mesmos.

## Contribuindo

Relatos de diferença entre o emulador e a API real são a contribuição mais valiosa. Veja o [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

[MIT](LICENSE). Sem vínculo com o Asaas nem endosso dele; "Asaas" é marca do seu titular. O emulador nunca fala com a API real, exceto nos testes de contrato opcionais.
