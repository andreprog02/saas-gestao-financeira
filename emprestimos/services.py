from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import List, Tuple
from datetime import date

from dateutil.relativedelta import relativedelta


@dataclass(frozen=True)
class ParcelaGerada:
    numero: int
    vencimento: date
    valor: Decimal


def round_centena_superior(valor: Decimal) -> Decimal:
    """
    Arredonda sempre para a centena superior.
    Ex:
      1789.00 -> 1800.00
      1800.00 -> 1800.00
      1801.00 -> 1900.00
    """
    valor = Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    centenas = (valor / Decimal("100")).to_integral_value(rounding=ROUND_CEILING)
    return (centenas * Decimal("100")).quantize(Decimal("0.00"))


def parcela_price(valor_emprestado: Decimal, taxa_pct: Decimal, n: int) -> Decimal:
    """
    Tabela Price:
      PMT = PV * [ i * (1+i)^n ] / [ (1+i)^n - 1 ]
    taxa_pct: ex 5.00 = 5% ao mês
    """
    if n <= 0:
        raise ValueError("Quantidade de parcelas deve ser >= 1")

    pv = Decimal(valor_emprestado).quantize(Decimal("0.01"))
    i = (Decimal(taxa_pct) / Decimal("100")).quantize(Decimal("0.0000001"))

    if i == 0:
        return (pv / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    fator = (Decimal("1") + i) ** n
    pmt = pv * (i * fator) / (fator - Decimal("1"))
    return pmt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def simular(
    valor_emprestado: Decimal,
    qtd_parcelas: int,
    taxa_juros_mensal: Decimal,
    primeiro_vencimento: date,
) -> Tuple[Decimal, Decimal, Decimal, Decimal, List[ParcelaGerada]]:
    """
    Retorna:
    - parcela_bruta
    - parcela_aplicada (arredondada p/ centena superior)
    - total_contrato (aplicado)
    - ajuste_arredondamento (aplicado - bruto)
    - lista de parcelas (valor aplicado)
    """
    parcela_bruta = parcela_price(valor_emprestado, taxa_juros_mensal, qtd_parcelas)
    parcela_aplicada = round_centena_superior(parcela_bruta)

    total_bruto = (parcela_bruta * Decimal(qtd_parcelas)).quantize(Decimal("0.01"))
    total_aplicado = (parcela_aplicada * Decimal(qtd_parcelas)).quantize(Decimal("0.01"))
    ajuste = (total_aplicado - total_bruto).quantize(Decimal("0.01"))

    parcelas: List[ParcelaGerada] = []
    for k in range(1, qtd_parcelas + 1):
        venc = primeiro_vencimento + relativedelta(months=(k - 1))
        parcelas.append(ParcelaGerada(numero=k, vencimento=venc, valor=parcela_aplicada))

    return parcela_bruta, parcela_aplicada, total_aplicado, ajuste, parcelas
