from django.core.management.base import BaseCommand
from financeiro.models import CodigoOperacao


CODIGOS_PADRAO = [
    ("01", "Abertura de Caixa (Receb. Tesouraria)", "E", True, False),
    ("02", "Depósito em Dinheiro", "E", True, False),
    ("03", "Saque em Espécie", "S", True, False),
    ("04", "Envio para Tesouraria", "S", True, False),
    ("05", "Recebimento da Tesouraria", "E", True, False),
    ("06", "Depósito Bancário", "S", True, False),
    ("07", "Saque Bancário", "E", True, False),
    ("08", "Saque Conta Corrente Cliente", "E", True, True),
    ("09", "Depósito Conta Corrente Cliente", "S", True, True),
    ("10", "Pagamento de Prestação (Físico)", "E", True, True),
    ("11", "Despesas Diversas", "S", True, False),
    ("20", "Liberação de Crédito (Eletrônico)", "S", False, True),
    ("21", "Recebimento Transferência (Eletrônico)", "E", False, True),
    ("22", "Pagamento Prestação (Eletrônico)", "E", False, True),
    ("23", "Transferência Bancária (Eletrônico)", "S", False, False),
]


class Command(BaseCommand):
    help = "Cria os códigos de operação padrão do caixa"

    def handle(self, *args, **options):
        criados = 0
        for codigo, desc, tipo, fisico, cliente in CODIGOS_PADRAO:
            _, created = CodigoOperacao.objects.get_or_create(
                codigo=codigo,
                defaults={
                    "descricao": desc,
                    "tipo": tipo,
                    "afeta_caixa_fisico": fisico,
                    "exige_cliente": cliente,
                }
            )
            if created:
                criados += 1
                self.stdout.write(f"  ✓ {codigo} — {desc}")

        self.stdout.write(self.style.SUCCESS(f"\n{criados} códigos criados."))
