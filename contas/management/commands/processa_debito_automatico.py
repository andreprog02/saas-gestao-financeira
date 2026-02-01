from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from contas.models import ContaCorrente, MovimentacaoConta
from emprestimos.models import Parcela, ParcelaStatus

class Command(BaseCommand):
    help = 'Processa débito automático de parcelas vencendo hoje ou atrasadas para clientes com saldo.'

    def handle(self, *args, **kwargs):
        hoje = timezone.localdate()
        
        # Busca parcelas ABERTAS que vencem hoje ou antes (atrasadas)
        parcelas_cobraveis = Parcela.objects.filter(
            status=ParcelaStatus.ABERTA,
            vencimento__lte=hoje
        ).select_related('emprestimo', 'emprestimo__cliente', 'emprestimo__cliente__conta_corrente')

        count = 0
        for parcela in parcelas_cobraveis:
            try:
                # Verifica se o cliente tem conta criada
                if not hasattr(parcela.emprestimo.cliente, 'conta_corrente'):
                    continue
                
                conta = parcela.emprestimo.cliente.conta_corrente
                
                # Calcula valor atualizado (com juros/multa se houver atraso)
                # Assumindo que você tem o método 'dados_atualizados' no model Parcela
                dados = parcela.dados_atualizados
                valor_total = dados['total']

                # Verifica se tem saldo suficiente
                if conta.saldo >= valor_total:
                    with transaction.atomic():
                        # 1. Debita da conta
                        MovimentacaoConta.objects.create(
                            conta=conta,
                            tipo='DEBITO',
                            origem='PAGAMENTO_PARCELA',
                            valor=valor_total,
                            descricao=f"Débito Automático Parc. {parcela.numero}/{parcela.emprestimo.qtd_parcelas}",
                            parcela=parcela,
                            emprestimo=parcela.emprestimo
                        )

                        # 2. Baixa a parcela
                        parcela.marcar_como_paga(valor_pago=valor_total, data_pagamento=hoje)
                        
                        self.stdout.write(self.style.SUCCESS(f"Debitado: {parcela} - R$ {valor_total}"))
                        count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Erro ao processar parcela {parcela.id}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Processamento finalizado. {count} parcelas debitadas."))