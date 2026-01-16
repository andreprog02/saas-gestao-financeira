from django.db.models import Max
from django.utils import timezone
from .models import Emprestimo


def gerar_codigo_contrato() -> str:
    ano = timezone.localdate().year
    prefix = f"CTR-{ano}-"
    ultimo = Emprestimo.objects.filter(codigo_contrato__startswith=prefix).aggregate(m=Max("codigo_contrato"))["m"]

    if not ultimo:
        seq = 1
    else:
        seq = int(ultimo.split("-")[-1]) + 1

    return f"{prefix}{seq:06d}"
