"""Geração das folhas de etiquetas — QR + nome, N por folha A4.

Este módulo é **puro**: não importa `streamlit` nem toca no banco. Recebe uma
lista de itens (`{nome, codigo_qr, codigos_extras, posicoes, ...}`) e devolve
HTML autocontido, pronto para o navegador imprimir com Ctrl+P. Isso é o que
permite testar a geração sem subir o app (`tests/test_etiquetas.py`).

Decisões que valem a pena registrar (herdadas da aba 🏷️ Etiquetas do
`camda-estoque`, de onde os dados vêm):

1. **Uma etiqueta por PRODUTO, não por linha de `estoque_mestre`.** Um produto
   da CAMDA pode ter mais de um código ativo ('254185' e 'US254185'), cada um
   com sua linha e seu saldo. Duas etiquetas do mesmo produto no mesmo rack
   fazem alguém contar o produto duas vezes no inventário. O agrupamento é
   feito em `dados.py`.

2. **O QR carrega só o código, como texto puro.** Sem URL e sem encurtador: o
   app de galpão consulta o banco por conta própria (funciona sem internet),
   nenhum domínio pode expirar e derrubar as etiquetas do galpão inteiro, e o
   conteúdo curto gera um símbolo de 21×21 módulos — quadrados grandes, lê de
   longe e tolera sujeira.

3. **`micro=False` é obrigatório.** Para entrada numérica curta o segno
   escolheria um Micro QR (M4), que o `mobile_scanner`/ML Kit usado pelo app
   NÃO lê. O símbolo tem que ser QR padrão.

4. **`border=4` (zona de silêncio) não se negocia.** A margem branca em volta
   é o que faz o leitor achar o código; reduzi-la para engordar o símbolo
   troca leitura garantida por 20% de tamanho.

5. **Nenhuma medida escrita à mão.** A célula sai da aritmética da folha
   ((folha − 2×margem − gaps) / n) e o QR sai da célula, descontado o bloco de
   texto. Por isso qualquer quantidade por folha funciona — de 1 a 40 — sem
   preset novo: é este módulo que calcula o tamanho, não uma tabela.

6. **Impressão via download de HTML, não PDF.** O arquivo é autocontido (SVGs
   embutidos, zero rede) e o A4 vem do `@page` do CSS.
"""

import html as _esc
from math import ceil, log

try:
    import segno
    SEGNO_ERRO = ""
except ImportError as _exc:  # pragma: no cover - ambiente sem a dependência
    segno = None
    SEGNO_ERRO = str(_exc)


# ── Limites ───────────────────────────────────────────────────────────────────
# Teto de etiquetas por folha: abaixo de ~18 mm de célula o QR fica com módulos
# menores que 0,4 mm e a impressora a laser comum começa a borrar o símbolo.
MAX_POR_FOLHA = 40
MIN_CELULA_MM = 18.0

# Papéis disponíveis (largura, altura) em mm, retrato.
PAPEIS = {
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "Carta": (215.9, 279.4),
}

# Conversão de pt para mm (1 pt = 1/72 pol).
_PT_MM = 25.4 / 72.0


def folha_mm(papel: str, paisagem: bool) -> tuple:
    """(largura, altura) da folha em mm, já considerando a orientação."""
    largura, altura = PAPEIS.get(papel, PAPEIS["A4"])
    return (altura, largura) if paisagem else (largura, altura)


# ── Layout ────────────────────────────────────────────────────────────────────

def grade_auto(por_folha: int, pagina: tuple, margem_mm: float, gap_mm: float) -> tuple:
    """Escolhe (colunas, linhas) para caber `por_folha` etiquetas na página.

    Critérios, nesta ordem:

    1. **Menos células desperdiçadas.** 8 por folha vira 2×4 (exato), nunca
       3×3 com uma célula vazia. Só quando o número é primo e grande (7, 11) é
       que sobra célula — aí a última fica em branco, que é preferível a
       reduzir a quantidade pedida sem avisar.
    2. **Célula mais próxima do formato da etiqueta.** A etiqueta é um QR
       quadrado com uma faixa de texto embaixo, então o alvo é uma célula um
       pouco mais alta que larga (`_ASPECTO_ALVO`). Sem isso, 8 por folha
       poderia sair 8×1 — oito tiras de 22 mm de largura, com QR minúsculo.

    Combinações cuja célula fique menor que `MIN_CELULA_MM` são descartadas; se
    todas forem descartadas (folha pequena e muitas etiquetas), devolve a menos
    ruim, e quem chama avisa na tela.
    """
    _ASPECTO_ALVO = 0.85  # largura/altura desejada da célula
    largura, altura = pagina

    melhor = None
    melhor_fallback = None
    for colunas in range(1, por_folha + 1):
        linhas = ceil(por_folha / colunas)
        celula_l = (largura - 2 * margem_mm - gap_mm * (colunas - 1)) / colunas
        celula_a = (altura - 2 * margem_mm - gap_mm * (linhas - 1)) / linhas
        if celula_l <= 1 or celula_a <= 1:
            continue
        desperdicio = colunas * linhas - por_folha
        aspecto = abs(log((celula_l / celula_a) / _ASPECTO_ALVO))
        nota = (desperdicio, aspecto)

        cabe = min(celula_l, celula_a) >= MIN_CELULA_MM
        if cabe and (melhor is None or nota < melhor[0]):
            melhor = (nota, colunas, linhas)
        if melhor_fallback is None or nota < melhor_fallback[0]:
            melhor_fallback = (nota, colunas, linhas)

    escolha = melhor or melhor_fallback or ((0, 0), 1, por_folha)
    return escolha[1], escolha[2]


def medidas(cfg: dict) -> dict:
    """Todas as medidas da folha, derivadas da aritmética da página.

    O QR fica com o que sobra da célula depois do bloco de texto: a altura do
    texto é calculada em mm a partir do corpo da fonte (pt → mm), não chutada,
    senão o nome empurra o QR para fora da célula em fontes grandes.

    `qr_mm` é o lado do QR IMPRESSO, **incluindo** a zona de silêncio de 4
    módulos — é o que se mede na folha com a régua. Numa etiqueta de 30 mm o
    símbolo em si fica com ~21,7 mm.
    """
    largura, altura = cfg["folha_mm"]
    colunas, linhas = cfg["colunas"], cfg["linhas"]
    margem, gap = cfg["margem_mm"], cfg["gap_mm"]

    celula_l = (largura - 2 * margem - gap * (colunas - 1)) / colunas
    celula_a = (altura - 2 * margem - gap * (linhas - 1)) / linhas

    # Corpo do nome: proporcional à largura da célula, para o texto ocupar
    # sempre a mesma fração da etiqueta. /4.5 reproduz os 7 pt que a folha de
    # 30 por A4 (célula de 33,2 mm) usa hoje no camda-estoque.
    nome_pt = cfg.get("nome_pt") or max(5.5, min(16.0, celula_l / 4.5))

    pad = 1.0  # respiro interno da célula (mm), para a tesoura não comer letra
    linhas_texto = cfg.get("linhas_nome", 1)
    alt_texto = linhas_texto * nome_pt * 1.12 * _PT_MM + 0.8
    if cfg.get("mostrar_codigo"):
        alt_texto += nome_pt * 0.8 * 1.12 * _PT_MM + 0.4
    if cfg.get("mostrar_lote"):
        # duas linhas: lote em destaque + validade
        alt_texto += nome_pt * 1.15 * 1.12 * _PT_MM + nome_pt * 0.75 * 1.12 * _PT_MM + 0.8

    disponivel = min(celula_l - 2 * pad, celula_a - 2 * pad - alt_texto)
    qr_mm = max(8.0, disponivel) * float(cfg.get("qr_escala", 1.0))

    return {
        "celula_l": celula_l,
        "celula_a": celula_a,
        "util_l": largura - 2 * margem,
        "util_a": altura - 2 * margem,
        "qr_mm": qr_mm,
        "nome_pt": nome_pt,
        "pad_mm": pad,
        "alt_texto_mm": alt_texto,
        "por_folha": cfg["por_folha"],
        "modulo_mm": 0.0,  # preenchido por quem souber o número de módulos
    }


def cfg_layout(*, por_folha: int, papel: str = "A4", paisagem: bool = False,
               margem_mm: float = 10.0, gap_mm: float = 4.0,
               colunas: int = 0, linhas: int = 0, qr_escala: float = 1.0,
               nome_pt: float = 0.0, linhas_nome: int = 1,
               mostrar_codigo: bool = True, mostrar_lote: bool = False,
               linhas_corte: bool = True) -> dict:
    """Monta o dicionário de configuração da folha.

    `colunas`/`linhas` em 0 significam "decida por mim" (`grade_auto`). Quando
    vêm preenchidos, `por_folha` é recalculado como colunas × linhas: a grade
    manual é a fonte da verdade, senão a última linha da folha sairia cortada
    sem explicação.
    """
    por_folha = max(1, min(MAX_POR_FOLHA, int(por_folha)))
    pagina = folha_mm(papel, paisagem)

    if colunas and linhas:
        colunas, linhas = int(colunas), int(linhas)
        por_folha = colunas * linhas
    else:
        colunas, linhas = grade_auto(por_folha, pagina, margem_mm, gap_mm)

    return {
        "papel": papel,
        "paisagem": paisagem,
        "folha_mm": pagina,
        "por_folha": por_folha,
        "colunas": colunas,
        "linhas": linhas,
        "margem_mm": float(margem_mm),
        "gap_mm": float(gap_mm),
        "qr_escala": float(qr_escala),
        "nome_pt": float(nome_pt),
        "linhas_nome": int(linhas_nome),
        "mostrar_codigo": bool(mostrar_codigo),
        "mostrar_lote": bool(mostrar_lote),
        "linhas_corte": bool(linhas_corte),
    }


# ── QR ────────────────────────────────────────────────────────────────────────

_CACHE_QR: dict = {}


def svg_qr(codigo: str) -> tuple:
    """`(svg_inline, lado_svg, modulos_do_simbolo)` — medidas em módulos.

    `error='q'` (25% de tolerância a dano) e `border=4` não são estética: são o
    que decide se lê com poeira, vinco e rasgo. O segno pode promover o nível
    de correção para além de Q quando couber no mesmo tamanho; 'q' é o piso.

    O `viewBox` é acrescentado à mão: o segno emite só width/height, e sem ele
    o CSS que estica o QR apenas amplia a *viewport* — o desenho continua no
    tamanho original e a folha sai com um pedaço do canto do código em vez do
    símbolo inteiro.

    O cache é um dict de módulo (e não `@st.cache_data`) porque este arquivo é
    puro; o app envolve a função de novo se quiser cache entre reruns.
    """
    if codigo in _CACHE_QR:
        return _CACHE_QR[codigo]
    qr = segno.make(codigo, error="q", micro=False)
    modulos_simbolo = qr.symbol_size(scale=1, border=0)[0]
    lado = qr.symbol_size(scale=1, border=4)[0]
    svg = qr.svg_inline(scale=1, border=4).replace(
        "<svg ",
        f'<svg viewBox="0 0 {lado} {lado}" preserveAspectRatio="xMidYMid meet" '
        'shape-rendering="crispEdges" ',
        1,
    )
    _CACHE_QR[codigo] = (svg, lado, modulos_simbolo)
    return _CACHE_QR[codigo]


def mm_por_modulo(codigo: str, qr_mm: float) -> float:
    """Lado de UM módulo impresso, em mm — a medida que decide se lê ou não.

    Abaixo de ~0,4 mm por módulo a impressora a laser comum não resolve o
    quadrado e o leitor erra. É isso que o app mostra como alerta em vez de
    deixar a pessoa descobrir com a folha já impressa.
    """
    _svg, lado, _mod = svg_qr(codigo)
    return qr_mm / lado


# ── HTML ──────────────────────────────────────────────────────────────────────

def _css_grade(cfg: dict, *, documento: bool) -> str:
    """CSS da folha a partir da configuração.

    `documento=False` devolve só as regras da grade e da célula, para a prévia
    dentro do Streamlit — as regras de `@page`/`body` não podem entrar na
    página do app, senão reformatam o dashboard inteiro. `documento=True`
    acrescenta essas regras, para o arquivo baixado.

    O nome é truncado com reticências pelo próprio navegador
    (`text-overflow`/`-webkit-line-clamp`) em vez de por contagem de
    caracteres: quem sabe quantos caracteres cabem na célula é o motor de texto
    que vai imprimir, com a fonte e o kerning reais.
    """
    med = medidas(cfg)
    largura, altura = cfg["folha_mm"]
    nome_pt = med["nome_pt"]

    if cfg.get("linhas_nome", 1) > 1:
        clamp = f"""
  display: -webkit-box;
  -webkit-line-clamp: {cfg['linhas_nome']};
  -webkit-box-orient: vertical;
  overflow: hidden;"""
    else:
        clamp = """
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;"""

    corte = (
        "border: 0.2mm dashed #b0b0b0;" if cfg.get("linhas_corte") else "border: none;"
    )

    regras = f"""
.etq-folha {{
  display: grid;
  grid-template-columns: repeat({cfg['colunas']}, {med['celula_l']:.3f}mm);
  grid-auto-rows: {med['celula_a']:.3f}mm;
  gap: {cfg['gap_mm']}mm;
  justify-content: start;
  align-content: start;
  width: {med['util_l']:.3f}mm;
  /* Repetido aqui (o `body` do documento já traz) para a prévia dentro do
     Streamlit não herdar a fonte e a cor do tema escuro do dashboard. */
  font-family: Helvetica, Arial, "Liberation Sans", sans-serif;
  color: #000;
}}
.etq-cel {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  overflow: hidden;
  text-align: center;
  box-sizing: border-box;
  padding: {med['pad_mm']}mm;
  {corte}
}}
.etq-cel-qr {{ width: {med['qr_mm']:.3f}mm; max-width: 100%; line-height: 0; }}
.etq-cel-qr svg {{ width: 100%; height: auto; display: block; }}
.etq-nome {{
  font-size: {nome_pt:.2f}pt;
  font-weight: 700;
  line-height: 1.12;
  margin-top: 0.8mm;
  text-transform: uppercase;
  /* Ocupa a célula inteira (mais largo que o QR, para caber mais nome). */
  box-sizing: border-box;
  width: 100%;{clamp}
}}
.etq-cod {{
  font-size: {nome_pt * 0.8:.2f}pt;
  font-weight: 600;
  font-family: "Courier New", monospace;
  margin-top: 0.4mm;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
}}
.etq-lote {{
  font-size: {nome_pt * 1.15:.2f}pt;
  font-weight: 700;
  font-family: "Courier New", monospace;
  margin-top: 0.6mm;
  word-break: break-all;
  line-height: 1.05;
}}
.etq-validade {{
  font-size: {nome_pt * 0.75:.2f}pt;
  font-weight: 700;
  color: #cc0000;
  margin-top: 0.2mm;
}}
"""
    if not documento:
        return regras

    return f"""
@page {{ size: {largura}mm {altura}mm; margin: {cfg['margem_mm']}mm; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #fff;
  color: #000;
  font-family: Helvetica, Arial, "Liberation Sans", sans-serif;
}}
.etq-folha {{ page-break-after: always; break-after: page; }}
.etq-folha:last-child {{ page-break-after: auto; break-after: auto; }}
{regras}
@media screen {{
  body {{ background: #e5e7eb; padding: 8mm; }}
  .etq-folha {{
    background: #fff;
    box-shadow: 0 2px 10px rgba(0,0,0,.2);
    margin: 0 auto 8mm;
    padding: {cfg['margem_mm']}mm;
    width: {largura}mm;
    box-sizing: content-box;
  }}
}}
"""


def bloco_etiqueta(item: dict, cfg: dict, lote=None) -> str:
    """UMA célula da grade: QR + nome (+ código, lote e validade se pedidos).

    Sem lote cadastrado as linhas de validade e lote são **omitidas** — nunca
    impressas vazias nem com 'None'.
    """
    nome = item.get("nome") or item["codigo_qr"]
    partes = [f'<div class="etq-cel-qr">{svg_qr(item["codigo_qr"])[0]}</div>',
              f'<div class="etq-nome" title="{_esc.escape(nome)}">'
              f"{_esc.escape(nome)}</div>"]

    if cfg.get("mostrar_codigo"):
        partes.append(f'<div class="etq-cod">{_esc.escape(item["codigo_qr"])}</div>')

    if cfg.get("mostrar_lote") and lote and lote.get("lote"):
        partes.append(f'<div class="etq-lote">{_esc.escape(str(lote["lote"]))}</div>')
        venc = lote.get("vencimento")
        if venc:
            partes.append(
                f'<div class="etq-validade">VAL {venc.strftime("%d/%m/%Y")}</div>'
            )

    return '<div class="etq-cel">' + "".join(partes) + "</div>"


def folhas(pares: list, cfg: dict) -> list:
    """Fatia os pares (item, lote) em folhas de `por_folha`. A última fica parcial.

    Uma folha = um `<div class="etq-folha">` com quebra de página depois, e não
    uma grade só que o navegador quebra sozinho: com grade contínua o Chrome
    corta linhas no meio da altura da página.
    """
    n = cfg["por_folha"]
    return [
        '<div class="etq-folha">'
        + "".join(bloco_etiqueta(i, cfg, l) for i, l in pares[inicio:inicio + n])
        + "</div>"
        for inicio in range(0, len(pares), n)
    ]


# Aviso de impressão, visível na TELA e ausente na folha.
#
# O navegador imprime data, título e a URL `file:///home/.../etiquetas.html`
# nas bordas quando "Cabeçalhos e rodapés" está marcado, e não existe CSS que
# desligue isso — a decisão é do diálogo de impressão. Por isso o aviso fica
# dentro do arquivo, onde quem imprime está olhando, e não só na aba do app.
_AVISO = """<div class="etq-aviso">
  <b>Para imprimir:</b> Ctrl+P → papel <b>{papel}</b>, <b>{orientacao}</b>,
  escala <b>100%</b> (não use “Ajustar à página”) e
  <b>Margens: Padrão</b>. Desmarque <b>“Cabeçalhos e rodapés”</b> para a data e
  o endereço do arquivo não saírem impressos na borda da folha.
  <span>Esta faixa não é impressa. {resumo}</span>
</div>"""

_CSS_AVISO = """
.etq-aviso {
  font-family: Helvetica, Arial, sans-serif;
  font-size: 11pt;
  line-height: 1.45;
  color: #111;
  background: #fff8c4;
  border: 1px solid #d9c257;
  border-radius: 6px;
  padding: 10px 14px;
  margin: 0 auto 8mm;
  max-width: 186mm;
}
.etq-aviso span { display: block; font-size: 9pt; color: #6b6b4a; margin-top: 4px; }
@media print { .etq-aviso { display: none !important; } }
"""


def resumo_layout(cfg: dict) -> str:
    """Frase única com a configuração da folha — vai para a tela e o arquivo."""
    med = medidas(cfg)
    return (
        f"{cfg['por_folha']} por folha ({cfg['colunas']} × {cfg['linhas']}) em "
        f"{cfg['papel']} {'paisagem' if cfg['paisagem'] else 'retrato'} · célula "
        f"{med['celula_l']:.1f} × {med['celula_a']:.1f} mm · QR "
        f"{med['qr_mm']:.1f} mm"
    )


def montar_documento(pares: list, cfg: dict, titulo: str) -> str:
    """Documento HTML completo e autocontido (SVGs embutidos, zero rede)."""
    aviso = _AVISO.format(
        papel=cfg["papel"],
        orientacao="paisagem" if cfg["paisagem"] else "retrato",
        resumo=_esc.escape(resumo_layout(cfg)),
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="pt-BR">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_esc.escape(titulo)}</title>\n"
        f"<style>{_css_grade(cfg, documento=True)}{_CSS_AVISO}</style>\n"
        "</head>\n<body>\n"
        + aviso
        + "\n"
        + "\n".join(folhas(pares, cfg))
        + "\n</body>\n</html>\n"
    )


def css_previa(cfg: dict) -> str:
    """CSS da prévia dentro do Streamlit (sem `@page`/`body`)."""
    return _css_grade(cfg, documento=False)


def n_folhas(total: int, cfg: dict) -> int:
    return ceil(total / cfg["por_folha"]) if total else 0
