# 🏷️ Etiquetas · CAMDA

Dashboard dedicado a **imprimir etiquetas com QR code** para os racks do
galpão. Os produtos vêm do mesmo banco Turso do dashboard
[`LeoLira1/camda-estoque`](https://github.com/LeoLira1/camda-estoque) (aba
🏷️ Etiquetas): a réplica libSQL sincroniza com a nuvem e as etiquetas saem do
que aquele dashboard gravou. **Este app só lê** — nenhum INSERT, UPDATE ou DDL.

O que ele acrescenta à aba original: a **quantidade de etiquetas por folha é
escolhida na hora** (1 a 40, além de papel e orientação), e grade, célula, QR e
corpo do texto são recalculados a partir dela. Não há tabela de presets para
manter — quer 8 produtos numa folha A4? Escolha 8 e a folha sai 2 × 4, com QR de
~52 mm.

## Como usar

1. **Etiquetas por folha** (barra lateral): 1, 2, 4, 6, 8, 9, 12, 16, 20, 24, 30
   ou qualquer valor até 40 em *Personalizado*.
2. **Filtre** por busca, rack ou categoria — e refine no multiselect se quiser
   só alguns produtos. Vazio = todos os filtrados.
3. Confira a **prévia da primeira folha**: é a folha de verdade, mesmas medidas
   em mm do arquivo baixado.
4. **Baixe o HTML**, abra no navegador e imprima com Ctrl+P: papel e orientação
   como configurados, escala **100%** (não use “Ajustar à página”), **Margens:
   Padrão**, e desmarque **“Cabeçalhos e rodapés”** — senão o navegador imprime
   a data e o caminho do arquivo na borda da folha.

O arquivo é autocontido (os QR estão embutidos como SVG): imprime sem internet.

### Quantas cabem, e o que isso faz com o QR

| Por folha | Grade (A4 retrato) | Célula | QR impresso | Leitura |
|---|---|---|---|---|
| 1  | 1 × 1 | 190 × 277 mm | 188 mm | corredor inteiro |
| 2  | 1 × 2 | 190 × 136 mm | 122 mm | de longe |
| 8  | 2 × 4 | 93 × 66 mm   | 52 mm  | de longe |
| 12 | 3 × 4 | 61 × 66 mm   | 53 mm  | de longe |
| 30 | 5 × 6 | 35 × 43 mm   | 33 mm  | de perto |
| 40 | 5 × 8 | 35 × 31 mm   | 22 mm  | de perto |

(Com 12 por folha a célula fica mais estreita que a de 8, mas tem a mesma
altura — e é a altura que limita o QR, por causa da faixa de texto embaixo.)

A regra prática: a distância de leitura fica em torno de **10× o lado do QR**.
O app avisa na tela quando cada quadradinho do símbolo fica abaixo de 0,40 mm —
o ponto em que a impressora a laser comum começa a borrar e o leitor erra.

Outros ajustes (expander **⚙️ Ajuste fino**): margem da folha, espaço entre
etiquetas, grade manual (colunas × linhas), tamanho do QR em %, corpo do nome,
nome em 2–3 linhas, imprimir o código embaixo do nome e linhas de corte
tracejadas.

### Lote e validade

Desligados por padrão. Ligados, cada etiqueta ganha o número do lote e a
validade — pelo critério FEFO (vencimento mais próximo) ou uma etiqueta por
lote cadastrado. Só cabe confortavelmente até ~8 por folha; o app avisa quando a
célula ficar apertada. O QR **não** muda com o lote: ele carrega só o código do
produto.

## Rodar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

Sem credenciais o app abre em **modo local**, lendo um `etiquetas_local.db`
vazio — útil para mexer no layout sem tocar no banco compartilhado.

### Credenciais

Em `.streamlit/secrets.toml` (não versionado), num `.env` ou como variáveis de
ambiente — as mesmas do `camda-estoque`:

```toml
TURSO_DATABASE_URL   = "libsql://....turso.io"
TURSO_AUTH_TOKEN     = "..."
CAMDA_ACCESS_PASSWORD = "..."   # opcional: sem ela o app abre direto
```

> ⚠️ Nunca aponte credenciais reais para um `etiquetas_local.db` criado em modo
> local: o libSQL responde `invalid local state: db file exists but metadata
> file does not`. Apague `etiquetas_local.db*` antes.

O botão **🔄 Sincronizar** puxa da nuvem o que o dashboard de estoque gravou
(os dados também expiram sozinhos a cada 5 min).

## Estrutura

| Arquivo | Papel |
|---|---|
| `app.py` | interface Streamlit: filtros, layout da folha, prévia, download |
| `dados.py` | conexão Turso + carga de produtos e lotes — **somente SELECT** |
| `etiquetas.py` | aritmética da folha, QR e HTML. Puro: sem Streamlit, sem banco |
| `tests/test_etiquetas.py` | testes do gerador (`python tests/test_etiquetas.py`) |

`etiquetas.py` é puro de propósito: o que quebra uma folha impressa é
aritmética de milímetro, e isso dá para testar sem navegador. Os testes conferem
que a grade nunca estoura a folha (em toda quantidade de 1 a 40, retrato e
paisagem), que o QR mais o texto cabem na célula, que o símbolo é QR padrão e
não Micro QR, e que o documento não depende de rede.

## Decisões que valem a pena saber

- **Uma etiqueta por PRODUTO, não por linha de `estoque_mestre`.** Um produto
  pode ter mais de um código ativo ('254185' e 'US254185'); duas etiquetas do
  mesmo produto no mesmo rack fazem alguém contar duas vezes no inventário.
- **O QR carrega só o código, como texto puro.** Sem URL e sem encurtador: o
  app de galpão consulta o banco por conta própria (funciona sem internet),
  nenhum domínio pode expirar e derrubar as etiquetas do galpão inteiro.
- **QR padrão, nunca Micro QR** (`micro=False`): o `mobile_scanner`/ML Kit do
  app não lê Micro QR.
- **`border=4` (zona de silêncio) não se negocia**: a margem branca em volta é
  o que faz o leitor achar o código.
- **HTML para impressão, não PDF.** O A4 vem do `@page` do CSS e o arquivo é
  autocontido; gerar PDF acrescentaria dependência sem melhorar a folha.
- **Streamlit fixado em 1.59.0** (e `starlette<1.4`), igual ao `camda-estoque`:
  atualização automática já derrubou aquele app.
