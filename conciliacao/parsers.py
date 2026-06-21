"""
Parsers para importação de extratos bancários.

Suporta:
- OFX (Open Financial Exchange) — padrão usado por Itaú, BB, Bradesco, etc.
- CSV genérico — com mapeamento configurável de colunas.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional


@dataclass
class LancamentoParsed:
    """Lançamento extraído de um arquivo de extrato."""
    data: date
    valor: Decimal
    descricao: str
    documento: str = ""
    tipo: str = ""  # C ou D

    def __post_init__(self):
        if not self.tipo:
            self.tipo = "C" if self.valor >= 0 else "D"
        if self.tipo == "D" and self.valor > 0:
            self.valor = -self.valor


# ==============================================================================
# PARSER OFX
# ==============================================================================

def parse_ofx(conteudo: str) -> List[LancamentoParsed]:
    """
    Faz parse de arquivo OFX (formato SGML usado por bancos brasileiros).
    
    OFX não é XML puro — tem um header antes do <OFX>.
    Extrai as tags <STMTTRN> que contêm as transações.
    """
    lancamentos = []

    # Remove o header OFX (tudo antes de <OFX>)
    match = re.search(r"<OFX>", conteudo, re.IGNORECASE)
    if not match:
        raise ValueError("Arquivo não parece ser OFX válido — tag <OFX> não encontrada.")
    
    xml_part = conteudo[match.start():]

    # OFX usa tags sem fechamento adequado, precisa fechar manualmente
    # Fecha tags abertas tipo <DTPOSTED>20250101 -> <DTPOSTED>20250101</DTPOSTED>
    xml_part = re.sub(
        r"<(\w+)>([^<\r\n]+)",
        r"<\1>\2</\1>",
        xml_part,
    )

    # Garante fechamento de tags container
    for tag in ["STMTTRN", "STMTTRNP"]:
        if f"<{tag}>" in xml_part and f"</{tag}>" not in xml_part:
            xml_part = xml_part.replace(f"<{tag}>", f"<{tag}>").replace(
                f"</{tag}>", f"</{tag}>"
            )

    try:
        root = ET.fromstring(xml_part)
    except ET.ParseError:
        # Fallback: parse por regex
        return _parse_ofx_regex(conteudo)

    # Busca todas as transações
    for trn in root.iter("STMTTRN"):
        try:
            dt_str = _get_text(trn, "DTPOSTED", "")[:8]  # YYYYMMDD
            dt = datetime.strptime(dt_str, "%Y%m%d").date()
            
            valor_str = _get_text(trn, "TRNAMT", "0").replace(",", ".")
            valor = Decimal(valor_str)
            
            descricao = _get_text(trn, "MEMO", "") or _get_text(trn, "NAME", "")
            documento = _get_text(trn, "CHECKNUM", "") or _get_text(trn, "REFNUM", "")

            lancamentos.append(LancamentoParsed(
                data=dt,
                valor=valor,
                descricao=descricao.strip(),
                documento=documento.strip(),
            ))
        except (ValueError, InvalidOperation):
            continue

    return lancamentos


def _parse_ofx_regex(conteudo: str) -> List[LancamentoParsed]:
    """Fallback: parse OFX por regex quando XML falha."""
    lancamentos = []
    
    blocos = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", conteudo, re.DOTALL | re.IGNORECASE)
    if not blocos:
        # Tenta sem tag de fechamento
        blocos = re.split(r"<STMTTRN>", conteudo)[1:]
    
    for bloco in blocos:
        try:
            dt_match = re.search(r"<DTPOSTED>(\d{8})", bloco)
            val_match = re.search(r"<TRNAMT>([^\s<]+)", bloco)
            memo_match = re.search(r"<MEMO>([^\r\n<]+)", bloco)
            name_match = re.search(r"<NAME>([^\r\n<]+)", bloco)
            check_match = re.search(r"<CHECKNUM>([^\r\n<]+)", bloco)

            if not dt_match or not val_match:
                continue

            dt = datetime.strptime(dt_match.group(1)[:8], "%Y%m%d").date()
            valor = Decimal(val_match.group(1).replace(",", "."))
            descricao = (memo_match.group(1) if memo_match else
                        name_match.group(1) if name_match else "Sem descrição")
            documento = check_match.group(1) if check_match else ""

            lancamentos.append(LancamentoParsed(
                data=dt,
                valor=valor,
                descricao=descricao.strip(),
                documento=documento.strip(),
            ))
        except (ValueError, InvalidOperation):
            continue

    return lancamentos


def _get_text(element, tag, default=""):
    """Helper: pega texto de sub-elemento XML."""
    el = element.find(tag)
    return el.text.strip() if el is not None and el.text else default


# ==============================================================================
# PARSER CSV
# ==============================================================================

def parse_csv(
    conteudo: str,
    col_data: int = 0,
    col_descricao: int = 1,
    col_valor: int = 2,
    col_documento: int = -1,
    formato_data: str = "%d/%m/%Y",
    separador: str = ";",
    pular_linhas: int = 1,
) -> List[LancamentoParsed]:
    """
    Faz parse de CSV genérico com mapeamento de colunas configurável.
    
    Args:
        conteudo: conteúdo do CSV como string
        col_data: índice da coluna de data
        col_descricao: índice da coluna de descrição
        col_valor: índice da coluna de valor
        col_documento: índice da coluna de documento (-1 = sem)
        formato_data: formato da data (ex: %d/%m/%Y)
        separador: delimitador do CSV (padrão: ;)
        pular_linhas: quantas linhas de cabeçalho pular
    """
    lancamentos = []

    reader = csv.reader(io.StringIO(conteudo), delimiter=separador)
    
    for i, row in enumerate(reader):
        if i < pular_linhas:
            continue
        
        if not row or all(c.strip() == "" for c in row):
            continue

        try:
            max_col = max(col_data, col_descricao, col_valor)
            if col_documento >= 0:
                max_col = max(max_col, col_documento)
            
            if len(row) <= max_col:
                continue

            # Data
            dt_str = row[col_data].strip().strip('"')
            dt = datetime.strptime(dt_str, formato_data).date()

            # Valor — formato brasileiro (1.234,56) ou americano (1234.56)
            valor_str = row[col_valor].strip().strip('"').replace("R$", "").replace(" ", "")
            
            if "," in valor_str and "." in valor_str:
                # Formato brasileiro: 1.234,56
                valor_str = valor_str.replace(".", "").replace(",", ".")
            elif "," in valor_str:
                # Só vírgula: 1234,56
                valor_str = valor_str.replace(",", ".")

            valor = Decimal(valor_str)

            # Descrição
            descricao = row[col_descricao].strip().strip('"')

            # Documento (opcional)
            documento = ""
            if col_documento >= 0 and len(row) > col_documento:
                documento = row[col_documento].strip().strip('"')

            lancamentos.append(LancamentoParsed(
                data=dt,
                valor=valor,
                descricao=descricao,
                documento=documento,
            ))
        except (ValueError, InvalidOperation, IndexError):
            continue

    return lancamentos
