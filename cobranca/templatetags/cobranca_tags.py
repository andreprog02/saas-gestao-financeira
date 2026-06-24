from django import template
from django.db.models import Sum
from decimal import Decimal

register = template.Library()


@register.simple_tag
def total_despesas(emprestimo):
    """Retorna o total de despesas de cobrança de um contrato."""
    total = emprestimo.despesas_cobranca.aggregate(s=Sum("valor"))["s"]
    return total or Decimal("0.00")
