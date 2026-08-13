"""Acesso ao banco do CAMDA Estoque — **somente leitura**.

Este dashboard é um consumidor do mesmo banco Turso/libSQL que o
`camda-estoque` (repositório `LeoLira1/camda-estoque`, aba 🏷️ Etiquetas). Os
dados não são copiados nem re-digitados aqui: a réplica local embarcada faz
`sync()` com a nuvem e as etiquetas saem do que o outro dashboard gravou.

Regras que este módulo respeita, e por quê:

* **Nenhum DDL, nenhum INSERT/UPDATE.** Só SELECT. É o que permite abrir este
  app com as mesmas credenciais sem risco de corromper o estoque — e por isso
  não usamos `get_codigos_produto` do `db_mapa`, que chama `ensure_mapa_tables`
  (DDL + COMMIT) a cada produto.
* **Toda consulta é tolerante a tabela ausente.** Um banco recém-criado pode
  não ter `mapa_produtos` ou `validade_lotes` ainda; a etiqueta continua
  saindo com o que houver em `estoque_mestre`.
* **Uma etiqueta por PRODUTO.** Um produto pode ter mais de um código ativo
  ('254185' e 'US254185'), cada um com sua linha em `estoque_mestre`. Duas
  etiquetas do mesmo produto no mesmo rack fazem alguém contar duas vezes no
  inventário — então os códigos são agrupados por `produto_id` do mapa.
"""

import os
from collections import defaultdict
from datetime import date, datetime

import streamlit as st

try:
    import libsql
    LIBSQL_ERRO = ""
except ImportError as _exc:  # pragma: no cover - ambiente sem a dependência
    libsql = None
    LIBSQL_ERRO = str(_exc)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


LOCAL_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "etiquetas_local.db"
)


def get_secret(chave: str) -> str:
    """Secret do Streamlit Cloud, do `.streamlit/secrets.toml` ou do ambiente.

    A ordem importa: no Cloud o segredo está em `st.secrets`; localmente é
    comum vir de um `.env` (carregado acima) ou de `export` no shell.
    """
    try:
        valor = st.secrets[chave]
        if valor:
            return str(valor)
    except (KeyError, FileNotFoundError, AttributeError):
        pass
    return os.environ.get(chave, "")


TURSO_DATABASE_URL = get_secret("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = get_secret("TURSO_AUTH_TOKEN")
USANDO_NUVEM = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)


@st.cache_resource(ttl=1800)
def _conexao():
    """Conexão com a réplica local embarcada, renovada a cada 30 min.

    Sem credenciais o app cai em modo local (arquivo `etiquetas_local.db`), o
    que serve para desenvolver sem tocar no banco compartilhado. **Nunca**
    aponte credenciais reais para um `etiquetas_local.db` que já exista de uma
    execução em modo local: o libSQL responde `invalid local state: db file
    exists but metadata file does not` — apague o arquivo nesse caso.
    """
    if USANDO_NUVEM:
        conn = libsql.connect(
            LOCAL_DB_PATH,
            sync_url=TURSO_DATABASE_URL,
            auth_token=TURSO_AUTH_TOKEN,
        )
        conn.sync()
    else:
        conn = libsql.connect(LOCAL_DB_PATH)
    return conn


def _reconectar():
    """Descarta a conexão e abre outra — usado quando o stream expira."""
    _conexao.clear()
    st.session_state.pop("_sincronizado", None)
    return _conexao()


def get_db():
    """Conexão pronta para uso, reconectando se o stream do Turso expirou.

    O Turso derruba streams ociosos e a mensagem que chega é um genérico
    `stream not found` / 404 no meio de um SELECT qualquer. Sem o probe abaixo
    o erro apareceria como exceção na tela do usuário no primeiro clique
    depois de um período parado.
    """
    conn = _conexao()
    if not USANDO_NUVEM:
        return conn

    if not st.session_state.get("_sincronizado"):
        try:
            conn.sync()
            st.session_state["_sincronizado"] = True
        except Exception as exc:
            if "stream not found" in str(exc) or "404" in str(exc):
                conn = _reconectar()
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        if "stream not found" in str(exc) or "404" in str(exc):
            conn = _reconectar()
    return conn


def sincronizar():
    """Puxa da nuvem o que o camda-estoque gravou desde o último sync."""
    st.session_state.pop("_sincronizado", None)
    carregar_produtos.clear()
    carregar_lotes.clear()
    if USANDO_NUVEM:
        try:
            _conexao().sync()
            st.session_state["_sincronizado"] = True
        except Exception:
            _reconectar()


def norm_codigo(codigo):
    """Forma canônica do código: UPPER(TRIM()). Vazio vira None."""
    if codigo is None:
        return None
    texto = str(codigo).strip().upper()
    return texto or None


def _parse_data(valor):
    """'2029-05-30' → date. Devolve None para vazio/inválido — nunca inventa."""
    txt = str(valor or "").strip()
    if not txt:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _select(conn, sql: str) -> list:
    """SELECT tolerante: tabela ausente devolve lista vazia, não exceção."""
    try:
        return conn.execute(sql).fetchall()
    except Exception:
        return []


# ── Universo de etiquetas ─────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def carregar_produtos(_conn) -> tuple:
    """`(itens, produtos_sem_codigo)` — um item por PRODUTO.

    Universo = produtos do mapa (agrupando todos os seus códigos) ∪ linhas de
    `estoque_mestre` cujo código não está vinculado a nenhum produto do mapa —
    para essas vale a linha única mesmo, não existe grupo de códigos.

    Cada item: `{chave, nome, codigo_qr, codigos_extras, categoria, qtd, racks,
    posicoes, no_mapa}`.

    O nome impresso vem de `estoque_mestre.produto` **do código que vai no QR**:
    o app de galpão exibe justamente a linha do código lido, então puxar o nome
    da mesma linha garante que a folha e a tela nunca divergem.
    `mapa_produtos.nome` só entra quando o código não tem linha em
    `estoque_mestre`.
    """
    estoque_por_cod = {}
    for cod, produto, categoria, qtd in _select(
        _conn, "SELECT codigo, produto, categoria, qtd_sistema FROM estoque_mestre"
    ):
        c = norm_codigo(cod)
        if not c:
            continue
        estoque_por_cod[c] = {
            "produto": str(produto or "").strip(),
            "categoria": str(categoria or "").strip(),
            "qtd": qtd or 0,
        }

    # Mapa de produtos: principal (mapa_produtos.codigo) + secundários
    # (mapa_produtos_codigos), todos normalizados. Duas queries, sem escrita.
    extras = defaultdict(set)
    for pid, cod in _select(
        _conn, "SELECT produto_id, codigo FROM mapa_produtos_codigos"
    ):
        c = norm_codigo(cod)
        if c:
            extras[pid].add(c)

    mapa = []
    for pid, nome, codigo in _select(
        _conn, "SELECT produto_id, nome, codigo FROM mapa_produtos ORDER BY nome"
    ):
        cods = set(extras.get(pid, set()))
        principal = norm_codigo(codigo)
        if principal:
            cods.add(principal)
        mapa.append({"produto_id": pid, "nome": nome, "codigo": principal,
                     "codigos": sorted(cods)})

    pos_por_pid = defaultdict(list)
    for pid, rua, pos_key in _select(
        _conn,
        "SELECT produto_id, rua, pos_key FROM mapa_posicoes "
        "WHERE produto_id IS NOT NULL ORDER BY rua, pos_key",
    ):
        pos_por_pid[pid].append((rua, pos_key))

    itens = []
    vinculados = set()
    sem_codigo = []

    for p in mapa:
        cods = [c for c in p["codigos"] if c]
        vinculados.update(cods)
        if not cods:
            # Sem código não há o que codificar no QR nem o que digitar no
            # campo manual do app. Reportado na tela em vez de ignorado.
            sem_codigo.append(p["nome"] or str(p["produto_id"]))
            continue

        ordenados = ([p["codigo"]] if p["codigo"] in cods else []) + [
            c for c in cods if c != p["codigo"]
        ]
        # O código do QR precisa ser um que tenha linha em estoque_mestre: é
        # dele que sai o nome que o app mostra ao ler a etiqueta.
        com_saldo = [c for c in ordenados if c in estoque_por_cod]
        codigo_qr = com_saldo[0] if com_saldo else ordenados[0]

        linha = estoque_por_cod.get(codigo_qr)
        posicoes = pos_por_pid.get(p["produto_id"], [])
        itens.append({
            "chave": f"pid:{p['produto_id']}",
            "nome": (linha or {}).get("produto") or str(p["nome"] or "").strip(),
            "codigo_qr": codigo_qr,
            "codigos_extras": [c for c in ordenados if c != codigo_qr],
            "categoria": (linha or {}).get("categoria", ""),
            "qtd": sum(
                estoque_por_cod[c]["qtd"] for c in ordenados if c in estoque_por_cod
            ),
            "racks": sorted({r for r, _ in posicoes}),
            "posicoes": [pk for _, pk in posicoes],
            "no_mapa": True,
        })

    for cod, linha in estoque_por_cod.items():
        if cod in vinculados:
            continue
        itens.append({
            "chave": f"cod:{cod}",
            "nome": linha["produto"],
            "codigo_qr": cod,
            "codigos_extras": [],
            "categoria": linha["categoria"],
            "qtd": linha["qtd"],
            "racks": [],
            "posicoes": [],
            "no_mapa": False,
        })

    itens.sort(key=lambda i: (i["nome"] or "", i["codigo_qr"]))
    return itens, sorted(set(sem_codigo))


# ── Lotes (validade_lotes) ────────────────────────────────────────────────────

def _ordem_fefo(lotes: list) -> list:
    """Ordena por vencimento crescente — FEFO. Sem data vai para o fim, para
    nunca ser escolhido por engano como “o mais próximo”."""
    return sorted(
        lotes,
        key=lambda l: (l["vencimento"] is None, l["vencimento"] or date.max, l["lote"]),
    )


def _split_produto_validade(produto):
    """'254185 - HERBICIDA BORAL 20L' → ('254185', 'HERBICIDA BORAL 20L').

    A planilha SIG traz o CÓDIGO junto do nome na coluna PRODUTO e o upload do
    camda-estoque grava a string inteira em `validade_lotes.produto`;
    `estoque_mestre` guarda só o nome. Sem prefixo devolve (None, texto).
    """
    import re
    s = str(produto or "").strip()
    m = re.match(r"^\s*([A-Z0-9\-]{3,20})\s*[-–]\s*(.+)$", s, re.IGNORECASE)
    if not m:
        return None, s
    return norm_codigo(m.group(1)), m.group(2).strip()


@st.cache_data(ttl=300, show_spinner=False)
def carregar_lotes(_conn) -> dict:
    """Lotes indexados por código E por nome.

    O índice por código é o que vale: casar pelo prefixo da planilha é exato.
    Casar por nome depende da grafia bater entre o BI e `estoque_mestre` — um
    espaço a mais ou um acento faz o lote sumir da etiqueta sem aviso. O índice
    por nome fica só como fallback, para as linhas sem prefixo de código.
    """
    por_codigo = defaultdict(list)
    por_nome = defaultdict(list)
    for produto, lote, vencimento, quantidade in _select(
        _conn, "SELECT produto, lote, vencimento, quantidade FROM validade_lotes"
    ):
        nome_lote = str(lote or "").strip()
        if not nome_lote:
            continue
        registro = {
            "lote": nome_lote,
            "vencimento": _parse_data(vencimento),
            "quantidade": quantidade or 0,
        }
        codigo, nome = _split_produto_validade(produto)
        if codigo:
            por_codigo[codigo].append(registro)
        por_nome[nome.upper()].append(registro)

    return {
        "por_codigo": {k: _ordem_fefo(v) for k, v in por_codigo.items()},
        "por_nome": {k: _ordem_fefo(v) for k, v in por_nome.items()},
    }


def lotes_do_item(item: dict, lotes: dict) -> list:
    """Todos os lotes do produto, em ordem FEFO.

    Procura pelo **grupo de códigos** do produto — o do QR mais os secundários.
    Lotes lançados sob '254185' e sob 'US254185' são o mesmo produto e entram na
    mesma etiqueta, com o FEFO considerando os dois. Só cai no match por nome
    quando nenhum código encontrou nada.
    """
    por_codigo = lotes.get("por_codigo", {})
    achados, vistos = [], set()
    for cod in [item["codigo_qr"], *item["codigos_extras"]]:
        for registro in por_codigo.get(cod, []):
            assinatura = (registro["lote"], registro["vencimento"])
            if assinatura in vistos:
                continue
            vistos.add(assinatura)
            achados.append(registro)
    if achados:
        return _ordem_fefo(achados)
    nome = _split_produto_validade(item.get("nome"))[1].upper()
    return lotes.get("por_nome", {}).get(nome, [])


def lote_fefo(item: dict, lotes: dict):
    """Lote de vencimento mais próximo do produto, ou None se não há lote."""
    disponiveis = lotes_do_item(item, lotes)
    return disponiveis[0] if disponiveis else None
