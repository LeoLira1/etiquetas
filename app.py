"""Dashboard de Etiquetas — CAMDA.

App Streamlit de página única, dedicado a **imprimir etiquetas com QR code**
para os racks do galpão. Os dados são os mesmos do dashboard
`LeoLira1/camda-estoque` (aba 🏷️ Etiquetas): a réplica libSQL sincroniza com o
Turso e as etiquetas saem do que o outro dashboard gravou. Nada é escrito aqui
— ver `dados.py`.

O que este app faz que a aba original não fazia: a **quantidade de etiquetas
por folha é escolhida na hora** (1 a 40), e todas as medidas — grade, célula,
QR, corpo do texto — são recalculadas a partir dela. Não existe tabela de
presets para manter.
"""

import hashlib
from datetime import date

import streamlit as st

import etiquetas as etq
from dados import (
    USANDO_NUVEM,
    LIBSQL_ERRO,
    carregar_lotes,
    carregar_produtos,
    get_db,
    get_secret,
    lotes_do_item,
    sincronizar,
)

st.set_page_config(
    page_title="Etiquetas · CAMDA",
    page_icon="🏷️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Trava de segurança contra engano de filtro. Não é por folha: o que custa caro
# é papel impresso, então o teto é de etiquetas geradas de uma vez.
MAX_ETIQUETAS = 1500

# Quantidades oferecidas de cara. Só números com fatoração boa em retrato —
# qualquer outro valor continua possível pelo campo "Personalizado", que aceita
# de 1 a etq.MAX_POR_FOLHA.
QTD_SUGERIDAS = [1, 2, 4, 6, 8, 9, 12, 16, 20, 24, 30]

CRIT_FEFO = "Vencimento mais próximo (FEFO)"
CRIT_TODOS = "Uma etiqueta por lote"
CRIT_SEM = "Sem lote"

_CSS = """<style>
.blk-title{font-size:1.35rem;font-weight:800;color:#e0e6ed;margin:0 0 2px;}
.blk-sub{font-size:0.82rem;color:#64748b;margin-bottom:14px;}
.blk-badge{display:inline-block;font-size:0.66rem;font-weight:700;letter-spacing:1px;
           text-transform:uppercase;border-radius:999px;padding:3px 10px;margin-bottom:10px;}
.blk-badge.on{background:rgba(34,197,94,.12);color:#22c55e;border:1px solid rgba(34,197,94,.35);}
.blk-badge.off{background:rgba(255,165,2,.12);color:#ffa502;border:1px solid rgba(255,165,2,.35);}
.blk-section{font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;
             color:#64748b;margin:16px 0 6px;padding-bottom:4px;border-bottom:1px solid #1e293b;}
.blk-kpi-row{display:flex;gap:8px;margin:10px 0 4px;flex-wrap:wrap;}
.blk-kpi{flex:1;min-width:92px;background:linear-gradient(135deg,#111827,#1a2332);
         border:1px solid #1e293b;border-radius:10px;padding:8px 10px;text-align:center;}
.blk-kpi-v{font-family:'JetBrains Mono',monospace;font-size:1.15rem;font-weight:700;color:#22c55e;}
.blk-kpi-v.blue{color:#3b82f6;} .blk-kpi-v.amber{color:#ffa502;}
.blk-kpi-l{font-size:0.58rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-top:2px;}
.blk-info{background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.25);
          border-radius:8px;padding:8px 12px;font-size:0.78rem;color:#93c5fd;margin:8px 0 12px;}
.blk-empty{text-align:center;padding:40px 20px;color:#475569;font-size:0.9rem;}
/* Prévia: a folha branca de verdade. As medidas da grade e da célula vêm de
   `etiquetas.css_previa()`, injetado junto — aqui só o papel.
   `box-sizing:content-box` é o que faz o padding somar POR FORA da largura:
   com border-box (o padrão do Streamlit) o miolo encolheria e as colunas,
   que somam a largura útil exata, vazariam para fora da folha. */
.blk-preview{background:#e5e7eb;border-radius:10px;padding:14px;overflow-x:auto;}
.blk-preview .etq-folha{background:#fff;border-radius:6px;margin:0 auto;padding:8mm;
                        box-shadow:0 2px 10px rgba(0,0,0,.2);box-sizing:content-box;}
</style>"""


# ── Acesso ────────────────────────────────────────────────────────────────────

def _tela_login(senha_esperada: str) -> bool:
    """Porta de entrada. Sem `CAMDA_ACCESS_PASSWORD` configurada, o app abre
    direto — é a mesma senha do camda-estoque, então quem já entra lá entra
    aqui, e um app sem senha configurada não deve virar uma tela morta."""
    if st.session_state.get("_liberado"):
        return True

    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown('<div class="blk-title">🏷️ Etiquetas · CAMDA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="blk-sub">Use a mesma senha de acesso do dashboard de estoque.</div>',
        unsafe_allow_html=True,
    )
    with st.form("login"):
        digitada = st.text_input("Senha de acesso", type="password")
        if st.form_submit_button("Entrar", type="primary"):
            # Comparação em bytes: `hmac.compare_digest` com str levanta
            # TypeError se houver caractere não-ASCII, e senha com acento é
            # comum aqui.
            import hmac
            if hmac.compare_digest(digitada.encode(), senha_esperada.encode()):
                st.session_state["_liberado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
    return False


# ── Painel de layout ──────────────────────────────────────────────────────────

def _painel_layout() -> dict:
    """Sidebar: quantas etiquetas por folha e como elas são desenhadas.

    A quantidade é o único controle que a pessoa precisa mexer — grade,
    tamanho da célula, tamanho do QR e corpo do texto saem dela. Os campos do
    expander existem para o caso de o papel/impressora fugirem do padrão, e
    todos têm um valor derivado como default.
    """
    st.sidebar.markdown('<div class="blk-section">📐 Folha</div>', unsafe_allow_html=True)

    opcoes = [str(n) for n in QTD_SUGERIDAS] + ["Personalizado"]
    escolha = st.sidebar.selectbox(
        "Etiquetas por folha", opcoes, index=opcoes.index("8"),
        help="Quantos produtos entram em cada folha. Quanto menos por folha, "
             "maior o QR e mais longe ele é lido.",
    )
    if escolha == "Personalizado":
        por_folha = int(st.sidebar.number_input(
            "Quantidade personalizada", min_value=1, max_value=etq.MAX_POR_FOLHA,
            value=8, step=1,
        ))
    else:
        por_folha = int(escolha)

    col_p, col_o = st.sidebar.columns(2)
    papel = col_p.selectbox("Papel", list(etq.PAPEIS.keys()), index=0)
    paisagem = col_o.selectbox("Orientação", ["Retrato", "Paisagem"], index=0) == "Paisagem"

    with st.sidebar.expander("⚙️ Ajuste fino"):
        margem = st.slider("Margem da folha (mm)", 5.0, 20.0, 10.0, 0.5)
        gap = st.slider("Espaço entre etiquetas (mm)", 0.0, 12.0, 4.0, 0.5)

        # Grade manual: só entra em cena quando marcada, e aí manda em
        # `por_folha` (colunas × linhas) — a alternativa seria uma grade que
        # não fecha com a quantidade pedida e uma última linha cortada.
        auto = st.checkbox(
            "Grade automática", value=True,
            help="Desmarque para dizer você mesmo quantas colunas e linhas. "
                 "A quantidade por folha passa a ser colunas × linhas.",
        )
        colunas = linhas = 0
        if not auto:
            sug_c, sug_l = etq.grade_auto(
                por_folha, etq.folha_mm(papel, paisagem), margem, gap
            )
            c1, c2 = st.columns(2)
            colunas = int(c1.number_input("Colunas", 1, 10, sug_c, 1))
            linhas = int(c2.number_input("Linhas", 1, 12, sug_l, 1))

        escala = st.slider(
            "Tamanho do QR (%)", 50, 100, 100, 5,
            help="100% = o maior QR que cabe na célula depois do texto.",
        ) / 100.0
        auto_fonte = st.checkbox("Corpo do texto automático", value=True)
        nome_pt = 0.0
        if not auto_fonte:
            nome_pt = st.slider("Corpo do nome (pt)", 5.0, 20.0, 9.0, 0.5)
        linhas_nome = int(st.selectbox(
            "Linhas para o nome", [1, 2, 3], index=0,
            help="Com 2 ou 3 linhas o nome quebra em vez de ser cortado com "
                 "reticências — cabe mais texto, sobra menos espaço para o QR.",
        ))
        mostrar_codigo = st.checkbox("Imprimir o código embaixo do nome", value=True,
                                     help="Serve para digitar à mão quando a "
                                          "etiqueta estiver danificada.")
        linhas_corte = st.checkbox("Linhas de corte tracejadas", value=True)

    return etq.cfg_layout(
        por_folha=por_folha, papel=papel, paisagem=paisagem,
        margem_mm=margem, gap_mm=gap, colunas=colunas, linhas=linhas,
        qr_escala=escala, nome_pt=nome_pt, linhas_nome=linhas_nome,
        mostrar_codigo=mostrar_codigo, linhas_corte=linhas_corte,
    )


# ── App ───────────────────────────────────────────────────────────────────────

def main():
    senha = get_secret("CAMDA_ACCESS_PASSWORD")
    if senha and not _tela_login(senha):
        return

    st.markdown(_CSS, unsafe_allow_html=True)

    if etq.segno is None:
        st.error(
            "❌ A biblioteca **segno** não está instalada — sem ela não há como "
            f"gerar o QR code. `pip install segno`. Detalhe: {etq.SEGNO_ERRO}"
        )
        return
    if LIBSQL_ERRO:
        st.error(
            "❌ A biblioteca **libsql** não está instalada — sem ela não há "
            f"como ler o estoque. `pip install libsql`. Detalhe: {LIBSQL_ERRO}"
        )
        return

    cfg = _painel_layout()

    st.markdown('<div class="blk-title">🏷️ Etiquetas de Rack</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="blk-sub">Folhas com QR code para colar na prateleira. O app '
        "de galpão lê o QR e mostra o saldo do produto. Os produtos vêm do mesmo "
        "banco do dashboard de estoque — este app <b>só lê</b>.</div>",
        unsafe_allow_html=True,
    )

    cab, botao = st.columns([4, 1])
    with cab:
        if USANDO_NUVEM:
            st.markdown('<span class="blk-badge on">☁️ sincronizado com o camda-estoque</span>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<span class="blk-badge off">⚠️ modo local · configure '
                        "TURSO_DATABASE_URL e TURSO_AUTH_TOKEN</span>",
                        unsafe_allow_html=True)
    with botao:
        if st.button("🔄 Sincronizar", use_container_width=True,
                     help="Puxa da nuvem o que o dashboard de estoque gravou."):
            sincronizar()
            st.rerun()

    conn = get_db()
    itens, sem_codigo = carregar_produtos(conn)
    lotes = carregar_lotes(conn)

    if not itens:
        st.markdown(
            '<div class="blk-empty">Nenhum produto disponível para etiqueta.<br>'
            "Carregue o estoque mestre no dashboard <b>camda-estoque</b> e "
            "clique em 🔄 Sincronizar.</div>",
            unsafe_allow_html=True,
        )
        return

    # ── Filtros ──────────────────────────────────────────────────────────────
    st.markdown('<div class="blk-section">🔎 Quais produtos</div>', unsafe_allow_html=True)
    racks_disp = sorted({r for i in itens for r in i["racks"]}, key=lambda r: (len(r), r))
    cats_disp = sorted({i["categoria"] for i in itens if i["categoria"]})

    c1, c2, c3 = st.columns([2, 1, 1])
    busca = c1.text_input("Buscar produto", placeholder="Nome ou código…",
                          label_visibility="collapsed")
    rack_sel = c2.selectbox("Rack", ["Todos os racks"] + racks_disp + ["Sem posição no mapa"],
                            label_visibility="collapsed")
    cat_sel = c3.selectbox("Categoria", ["Todas as categorias"] + cats_disp,
                           label_visibility="collapsed")

    filtrados = itens
    if rack_sel == "Sem posição no mapa":
        filtrados = [i for i in filtrados if not i["racks"]]
    elif rack_sel != "Todos os racks":
        filtrados = [i for i in filtrados if rack_sel in i["racks"]]
    if cat_sel != "Todas as categorias":
        filtrados = [i for i in filtrados if i["categoria"] == cat_sel]
    if busca.strip():
        termo = busca.strip().lower()
        filtrados = [
            i for i in filtrados
            if termo in (i["nome"] or "").lower()
            or termo in i["codigo_qr"].lower()
            or any(termo in c.lower() for c in i["codigos_extras"])
        ]

    if not filtrados:
        st.warning("Nenhum produto bate com os filtros.")
        return

    rotulos = {f'{i["codigo_qr"]} — {i["nome"]}': i for i in filtrados}
    escolhidos = st.multiselect(
        "Refinar seleção (vazio = todos os filtrados)", list(rotulos.keys()),
    )
    selecionados = [rotulos[r] for r in escolhidos] if escolhidos else filtrados

    col_cop, col_lote = st.columns([1, 2])
    copias = int(col_cop.number_input(
        "Cópias por produto", min_value=1, max_value=50, value=1, step=1,
        help="Repete cada etiqueta lado a lado na folha — para colar o mesmo "
             "produto em mais de uma posição.",
    ))
    com_lote = col_lote.checkbox(
        "Incluir lote e validade na etiqueta",
        value=False,
        help="Só cabe em etiquetas grandes. O QR carrega apenas o código do "
             "produto, que é o mesmo em todos os lotes.",
    )

    criterio = CRIT_SEM
    if com_lote:
        criterio = st.radio(
            "Qual lote imprimir?", [CRIT_FEFO, CRIT_TODOS], horizontal=True,
        )
        st.markdown(
            '<div class="blk-info">📌 ' + (
                "Cada produto sai com o lote de vencimento mais próximo — é o que "
                "deve ser retirado primeiro."
                if criterio == CRIT_FEFO else
                "Cada produto sai com <b>uma etiqueta por lote cadastrado</b>. "
                "Produtos com muitos lotes geram muitas etiquetas."
            ) + " Produtos sem lote cadastrado saem sem essas linhas.</div>",
            unsafe_allow_html=True,
        )
    cfg["mostrar_lote"] = com_lote

    # ── Pares (item, lote) ───────────────────────────────────────────────────
    pares = []
    for item in selecionados:
        if not com_lote:
            pares.append((item, None))
            continue
        disponiveis = lotes_do_item(item, lotes)
        if not disponiveis:
            pares.append((item, None))
        elif criterio == CRIT_TODOS:
            pares.extend((item, l) for l in disponiveis)
        else:
            pares.append((item, disponiveis[0]))  # FEFO: já ordenado
    if copias > 1:
        # Cópias adjacentes, não a lista repetida no fim: na folha as etiquetas
        # iguais saem lado a lado, que é como se corta e se leva para o rack.
        pares = [p for p in pares for _ in range(copias)]

    med = etq.medidas(cfg)
    n_folhas = etq.n_folhas(len(pares), cfg)

    st.markdown('<div class="blk-section">📄 Resultado</div>', unsafe_allow_html=True)
    kpis = [
        (len(selecionados), "Produtos", ""),
        (len(pares), "Etiquetas", ""),
        (n_folhas, "Folhas", "blue"),
        (f"{cfg['por_folha']}/folha", f"{cfg['colunas']} × {cfg['linhas']}", "blue"),
        (f"{med['qr_mm']:.0f} mm", "QR impresso", ""),
    ]
    st.markdown(
        '<div class="blk-kpi-row">'
        + "".join(
            f'<div class="blk-kpi"><div class="blk-kpi-v {c}">{v}</div>'
            f'<div class="blk-kpi-l">{r}</div></div>'
            for v, r, c in kpis
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    # ── Avisos de legibilidade ───────────────────────────────────────────────
    # Todos comparam medidas reais da folha; nenhum é palpite. É melhor a
    # pessoa saber antes de imprimir 40 folhas que o QR não vai ler.
    amostra = pares[0][0]["codigo_qr"]
    modulo = etq.mm_por_modulo(amostra, med["qr_mm"])
    if min(med["celula_l"], med["celula_a"]) < etq.MIN_CELULA_MM:
        st.warning(
            f"⚠️ Célula de {med['celula_l']:.1f} × {med['celula_a']:.1f} mm — "
            f"abaixo de {etq.MIN_CELULA_MM:.0f} mm o QR fica no limite do que a "
            "impressora resolve. Reduza a quantidade por folha, a margem ou o "
            "espaço entre etiquetas."
        )
    if modulo < 0.4:
        st.warning(
            f"⚠️ Cada quadradinho do QR sai com {modulo:.2f} mm. Abaixo de "
            "0,40 mm a impressora a laser comum borra o símbolo e o leitor "
            "erra. Diminua a quantidade por folha ou aumente o QR em "
            "**⚙️ Ajuste fino**."
        )
    if com_lote and med["celula_a"] < 45:
        st.info(
            "ℹ️ Com lote e validade a célula precisa de espaço: nesta densidade "
            "o texto pode ficar espremido. Até 8 por folha é o confortável."
        )
    if cfg["colunas"] * cfg["linhas"] != cfg["por_folha"]:
        st.caption(
            f"A grade {cfg['colunas']} × {cfg['linhas']} comporta "
            f"{cfg['colunas'] * cfg['linhas']} etiquetas; as "
            f"{cfg['colunas'] * cfg['linhas'] - cfg['por_folha']} últimas ficam "
            "em branco para respeitar a quantidade pedida."
        )
    if sem_codigo:
        st.info(
            f"ℹ️ {len(sem_codigo)} produto(s) do mapa não têm código vinculado e "
            "por isso não geram etiqueta (sem código não há QR nem digitação "
            f"manual): {', '.join(sem_codigo[:5])}"
            + ("…" if len(sem_codigo) > 5 else "")
        )

    if len(pares) > MAX_ETIQUETAS:
        st.error(
            f"❌ {len(pares)} etiquetas ({n_folhas} folhas) é mais do que o "
            f"gerador entrega de uma vez (limite {MAX_ETIQUETAS}). Filtre por "
            "rack ou categoria — o uso normal é reimprimir um rack de cada vez."
        )
        return

    # ── Prévia ───────────────────────────────────────────────────────────────
    st.markdown('<div class="blk-section">👁️ Prévia da primeira folha</div>',
                unsafe_allow_html=True)
    st.markdown(
        f"<style>{etq.css_previa(cfg)}</style>"
        '<div class="blk-preview">'
        f'{etq.folhas(pares[:cfg["por_folha"]], cfg)[0]}'
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"{etq.resumo_layout(cfg)} · quadradinho de {modulo:.2f} mm · "
        f"QR contém o texto puro `{amostra}` (sem URL, sem encurtador — o app "
        "resolve o código no banco por conta própria)."
    )

    # ── Download ─────────────────────────────────────────────────────────────
    st.markdown('<div class="blk-section">⬇️ Gerar folhas</div>', unsafe_allow_html=True)
    st.caption(
        f"Baixe o arquivo, abra no navegador e imprima com Ctrl+P: **{cfg['papel']}**, "
        f"**{'paisagem' if cfg['paisagem'] else 'retrato'}**, escala **100%** e "
        "**Margens: Padrão**. Desmarque **“Cabeçalhos e rodapés”** — senão o "
        "navegador imprime a data e o caminho do arquivo na borda da folha. O "
        "arquivo é autocontido: os QR codes estão dentro dele e não dependem de "
        "internet na hora de imprimir."
    )

    if rack_sel not in ("Todos os racks", "Sem posição no mapa"):
        sufixo, titulo = f"rack_{rack_sel.lower()}", f"Etiquetas — rack {rack_sel}"
    elif len(selecionados) == 1:
        sufixo = selecionados[0]["codigo_qr"].lower()
        titulo = f'Etiqueta — {selecionados[0]["nome"]}'
    else:
        sufixo, titulo = "geral", "Etiquetas de rack — CAMDA"

    documento = etq.montar_documento(pares, cfg, f"{titulo} ({cfg['por_folha']} por folha)")
    st.download_button(
        f"🏷️ Baixar {n_folhas} folha(s) · {len(pares)} etiquetas (HTML para impressão)",
        data=documento.encode("utf-8"),
        file_name=f"etiquetas_{sufixo}_{cfg['por_folha']}porfolha.html",
        mime="text/html",
        type="primary",
    )

    # A assinatura muda quando a seleção ou o layout mudam — serve para a
    # pessoa conferir que baixou o arquivo certo depois de mexer nos filtros.
    assinatura = hashlib.md5(documento.encode()).hexdigest()[:8]
    st.caption(
        f"Gerado em {date.today().strftime('%d/%m/%Y')} · versão `{assinatura}` · "
        f"{len(documento) / 1024:.0f} KB"
    )


if __name__ == "__main__":
    main()
