"""Testes do gerador de folhas — rodam sem Streamlit e sem banco.

`python tests/test_etiquetas.py` (ou `pytest tests/`). O módulo `etiquetas` é
puro justamente para isso: o que quebra a folha impressa é aritmética de
milímetro, e conferir isso num navegador é caro e não regride.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import etiquetas as etq  # noqa: E402

ITENS = [
    {"nome": "HERBICIDA BORAL 500 SC 20L", "codigo_qr": "254185",
     "codigos_extras": ["US254185"], "posicoes": ["A-01"]},
    {"nome": "ADJUVANTE AUREO 20L", "codigo_qr": "300112",
     "codigos_extras": [], "posicoes": []},
]


def _pares(n):
    return [(ITENS[i % len(ITENS)], None) for i in range(n)]


def test_grade_cabe_na_folha():
    """A soma das colunas + gaps + margens nunca pode passar da folha.

    É o defeito que não aparece na tela e só na impressora: a última coluna
    sai cortada. Vale para toda quantidade suportada, não só as sugeridas.
    """
    for n in range(1, etq.MAX_POR_FOLHA + 1):
        for paisagem in (False, True):
            cfg = etq.cfg_layout(por_folha=n, paisagem=paisagem)
            med = etq.medidas(cfg)
            largura, altura = cfg["folha_mm"]
            usado_l = (cfg["colunas"] * med["celula_l"]
                       + cfg["gap_mm"] * (cfg["colunas"] - 1) + 2 * cfg["margem_mm"])
            usado_a = (cfg["linhas"] * med["celula_a"]
                       + cfg["gap_mm"] * (cfg["linhas"] - 1) + 2 * cfg["margem_mm"])
            assert usado_l <= largura + 1e-6, (n, paisagem, usado_l, largura)
            assert usado_a <= altura + 1e-6, (n, paisagem, usado_a, altura)
            assert cfg["colunas"] * cfg["linhas"] >= n, (n, cfg)


def test_qr_cabe_na_celula():
    """O QR mais o bloco de texto têm que caber na altura da célula.

    Sem isso o `overflow:hidden` da célula corta o rodapé do símbolo — e um QR
    com um pedaço faltando não lê de jeito nenhum.
    """
    for n in (1, 4, 8, 12, 30, 40):
        for lote in (False, True):
            cfg = etq.cfg_layout(por_folha=n, mostrar_lote=lote, mostrar_codigo=True)
            med = etq.medidas(cfg)
            assert med["qr_mm"] <= med["celula_l"] - 2 * med["pad_mm"] + 1e-6
            assert (med["qr_mm"] + med["alt_texto_mm"]
                    <= med["celula_a"] - 2 * med["pad_mm"] + 1e-6), (n, lote, med)


def test_oito_por_folha_a4():
    """O caso pedido: 8 produtos numa folha A4 saem em 2 × 4, com QR grande.

    Números conferidos na aritmética da folha: (210 − 20 − 4)/2 = 93 mm de
    largura e (297 − 20 − 12)/4 = 66,25 mm de altura por célula.
    """
    cfg = etq.cfg_layout(por_folha=8)
    assert (cfg["colunas"], cfg["linhas"]) == (2, 4)
    med = etq.medidas(cfg)
    assert abs(med["celula_l"] - 93.0) < 0.01
    assert abs(med["celula_a"] - 66.25) < 0.01
    # A célula é mais larga que alta, então quem limita o QR é a ALTURA (66,25
    # menos o padding e a faixa de texto) — daí ~51 mm, e não os 93 da largura.
    # É QR de leitura de corredor: o alcance fica em torno de 10× o lado.
    assert 45 < med["qr_mm"] <= med["celula_a"] - 2 * med["pad_mm"]
    assert etq.mm_por_modulo("254185", med["qr_mm"]) > 1.0


def test_grade_manual_manda_na_quantidade():
    """Colunas × linhas informadas à mão substituem a quantidade pedida."""
    cfg = etq.cfg_layout(por_folha=8, colunas=3, linhas=5)
    assert cfg["por_folha"] == 15
    assert (cfg["colunas"], cfg["linhas"]) == (3, 5)


def test_fatiamento_em_folhas():
    """Cada folha recebe exatamente `por_folha` etiquetas; a última fica parcial."""
    cfg = etq.cfg_layout(por_folha=8)
    folhas = etq.folhas(_pares(19), cfg)
    assert len(folhas) == 3 == etq.n_folhas(19, cfg)
    assert folhas[0].count('class="etq-cel"') == 8
    assert folhas[-1].count('class="etq-cel"') == 3


def test_documento_autocontido():
    """O arquivo baixado não pode depender de rede na hora de imprimir."""
    cfg = etq.cfg_layout(por_folha=8)
    doc = etq.montar_documento(_pares(3), cfg, "Teste")
    assert "<svg" in doc and "src=" not in doc and "http" not in doc.replace(
        "http-equiv", ""
    )
    assert "@page" in doc and "size: 210.0mm 297.0mm" in doc
    # O aviso de impressão existe na tela e some no papel.
    assert "@media print { .etq-aviso { display: none !important; } }" in doc


def test_lote_vazio_nao_imprime_linha():
    """Sem lote a etiqueta sai sem as linhas — nunca com 'None' impresso."""
    cfg = etq.cfg_layout(por_folha=8, mostrar_lote=True)
    html = etq.bloco_etiqueta(ITENS[0], cfg, None)
    assert "None" not in html and "etq-lote" not in html


def test_qr_e_padrao_nao_micro():
    """Micro QR não é lido pelo ML Kit do app de galpão — tem que ser QR padrão.

    Um símbolo de versão 1 tem 21 módulos; um Micro QR M4 tem 17. Conferir o
    número de módulos é o que pega uma troca acidental de parâmetro no segno.
    """
    _svg, lado, modulos = etq.svg_qr("254185")
    assert modulos >= 21
    assert lado == modulos + 8  # border=4 de cada lado: a zona de silêncio


def test_escape_de_nome_com_html():
    """Nome com `<` não pode virar tag na folha."""
    item = {"nome": "PRODUTO <SCRIPT> 5L", "codigo_qr": "1", "codigos_extras": []}
    html = etq.bloco_etiqueta(item, etq.cfg_layout(por_folha=8))
    assert "<SCRIPT>" not in html and "&lt;SCRIPT&gt;" in html


if __name__ == "__main__":
    falhas = 0
    for nome, fn in sorted(globals().items()):
        if not nome.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ok   {nome}")
        except AssertionError as exc:
            falhas += 1
            print(f"  FALHA {nome}: {exc}")
    print("todos passaram" if not falhas else f"{falhas} falha(s)")
    sys.exit(1 if falhas else 0)
