"""
Comando de varredura diária de vencimentos.
Roda automaticamente (cron) ou manualmente: python manage.py processar_vencimentos

Fluxo:
1. Busca parcelas ABERTAS com vencimento <= hoje
2. Para cada parcela:
   a. Se tem cheque vinculado na custódia:
      - COMPENSADO → credita C/C, debita C/C, paga parcela
      - EM_CUSTODIA/ENVIADO → envia pra compensação se for o dia, não faz nada mais
      - DEVOLVIDO → marca parcela como pendente, permite reapresentação
   b. Se não tem cheque:
      - Verifica saldo C/C do cliente
      - Saldo >= parcela → paga total
      - 0 < Saldo < parcela → paga parcial (cria registro)
      - Saldo = 0 → não faz nada
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from emprestimos.models import Emprestimo, Parcela, ParcelaStatus
from contas.models import ContaCorrente, MovimentacaoConta
from financeiro.models import ChequeCustodia

logger = logging.getLogger("django")


class Command(BaseCommand):
    help = "Processa vencimentos do dia: cobra parcelas via C/C e cheques compensados"

    def add_arguments(self, parser):
        parser.add_argument(
            "--data", type=str, default="",
            help="Data a processar (YYYY-MM-DD). Default: hoje.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Simula sem efetuar pagamentos.",
        )

    def handle(self, *args, **options):
        data_ref = date.fromisoformat(options["data"]) if options["data"] else date.today()
        dry_run = options["dry_run"]

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  PROCESSAMENTO DE VENCIMENTOS — {data_ref.strftime('%d/%m/%Y')}")
        if dry_run:
            self.stdout.write("  *** MODO SIMULAÇÃO (dry-run) ***")
        self.stdout.write(f"{'='*60}\n")

        # Parcelas vencidas/vencendo hoje
        parcelas = Parcela.objects.filter(
            status=ParcelaStatus.ABERTA,
            vencimento__lte=data_ref,
        ).select_related("emprestimo__cliente").order_by("vencimento")

        total_parcelas = parcelas.count()
        pagas_total = 0
        pagas_parcial = 0
        cheques_enviados = 0
        sem_saldo = 0
        aguardando_cheque = 0

        self.stdout.write(f"  Parcelas em aberto até {data_ref.strftime('%d/%m/%Y')}: {total_parcelas}\n")

        for parcela in parcelas:
            cliente = parcela.emprestimo.cliente
            contrato = parcela.emprestimo

            self.stdout.write(f"  → Parcela #{parcela.numero} | {cliente.nome_completo} | "
                              f"R$ {parcela.valor} | Venc: {parcela.vencimento.strftime('%d/%m/%Y')}")

            # 1. Verifica se tem cheque vinculado (mesmo vencimento)
            cheque = ChequeCustodia.objects.filter(
                cliente=cliente,
                emprestimo=contrato,
                vencimento=parcela.vencimento,
            ).first()

            if cheque:
                resultado = self._processar_cheque(parcela, cheque, cliente, dry_run)
                if resultado == "PAGO":
                    pagas_total += 1
                elif resultado == "ENVIADO":
                    cheques_enviados += 1
                elif resultado == "AGUARDANDO":
                    aguardando_cheque += 1
                elif resultado == "DEVOLVIDO":
                    sem_saldo += 1
                continue

            # 2. Sem cheque — tenta debitar da C/C
            resultado = self._processar_cc(parcela, cliente, dry_run)
            if resultado == "TOTAL":
                pagas_total += 1
            elif resultado == "PARCIAL":
                pagas_parcial += 1
            else:
                sem_saldo += 1

        # Resumo
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  RESUMO:")
        self.stdout.write(f"    Pagas (total):       {pagas_total}")
        self.stdout.write(f"    Pagas (parcial):     {pagas_parcial}")
        self.stdout.write(f"    Cheques enviados:    {cheques_enviados}")
        self.stdout.write(f"    Aguardando cheque:   {aguardando_cheque}")
        self.stdout.write(f"    Sem saldo:           {sem_saldo}")
        self.stdout.write(f"{'='*60}\n")

    def _processar_cheque(self, parcela, cheque, cliente, dry_run):
        """Processa parcela com cheque vinculado."""

        if cheque.status == "COMPENSADO":
            # Cheque compensado → paga a parcela
            self.stdout.write(f"    ✓ Cheque {cheque.numero_cheque} COMPENSADO — liquidando parcela")
            if not dry_run:
                with transaction.atomic():
                    # Credita C/C do cliente (valor do cheque entrou)
                    self._creditar_cc(cliente, cheque.valor,
                                      f"Compensação cheque {cheque.numero_cheque}")
                    # Debita C/C pra pagar parcela
                    self._debitar_cc(cliente, parcela.valor,
                                     f"Pagamento parcela #{parcela.numero} — {parcela.emprestimo.codigo_contrato}")
                    # Marca parcela como paga
                    parcela.status = ParcelaStatus.PAGA
                    parcela.data_pagamento = date.today()
                    parcela.save()
                    parcela.emprestimo.atualizar_status()
            return "PAGO"

        elif cheque.status == "EM_CUSTODIA":
            # Dia do vencimento → envia pra compensação
            if parcela.vencimento <= date.today():
                self.stdout.write(f"    → Cheque {cheque.numero_cheque} enviado para compensação")
                if not dry_run:
                    cheque.status = "ENVIADO_COMPENSACAO"
                    cheque.data_envio_compensacao = date.today()
                    cheque.save()
                return "ENVIADO"
            return "AGUARDANDO"

        elif cheque.status == "ENVIADO_COMPENSACAO":
            self.stdout.write(f"    ⏳ Cheque {cheque.numero_cheque} aguardando compensação")
            return "AGUARDANDO"

        elif cheque.status == "DEVOLVIDO":
            self.stdout.write(f"    ✗ Cheque {cheque.numero_cheque} DEVOLVIDO — {cheque.motivo_devolucao}")
            return "DEVOLVIDO"

        return "AGUARDANDO"

    def _processar_cc(self, parcela, cliente, dry_run):
        """Tenta pagar parcela via saldo da C/C."""
        try:
            cc = ContaCorrente.objects.get(cliente=cliente)
        except ContaCorrente.DoesNotExist:
            self.stdout.write(f"    ✗ Sem conta corrente")
            return "SEM_SALDO"

        saldo = cc.saldo_atual if hasattr(cc, 'saldo_atual') else Decimal("0")

        # Calcula saldo real
        total_creditos = cc.movimentacoes.filter(tipo="CREDITO").aggregate(
            s=Sum("valor"))["s"] or Decimal("0")
        total_debitos = cc.movimentacoes.filter(tipo="DEBITO").aggregate(
            s=Sum("valor"))["s"] or Decimal("0")
        saldo = total_creditos - total_debitos

        if saldo <= 0:
            self.stdout.write(f"    ✗ Sem saldo (R$ {saldo:.2f})")
            return "SEM_SALDO"

        if saldo >= parcela.valor:
            # Pagamento total
            self.stdout.write(f"    ✓ Saldo R$ {saldo:.2f} ≥ Parcela R$ {parcela.valor} — PAGAMENTO TOTAL")
            if not dry_run:
                with transaction.atomic():
                    self._debitar_cc(cliente, parcela.valor,
                                     f"Pagamento parcela #{parcela.numero} — {parcela.emprestimo.codigo_contrato}")
                    parcela.status = ParcelaStatus.PAGA
                    parcela.data_pagamento = date.today()
                    parcela.save()
                    parcela.emprestimo.atualizar_status()
            return "TOTAL"
        else:
            # Pagamento parcial
            self.stdout.write(f"    ~ Saldo R$ {saldo:.2f} < Parcela R$ {parcela.valor} — PAGAMENTO PARCIAL")
            if not dry_run:
                with transaction.atomic():
                    self._debitar_cc(cliente, saldo,
                                     f"Pagamento PARCIAL parcela #{parcela.numero} — {parcela.emprestimo.codigo_contrato}")
                    # Reduz valor da parcela
                    parcela.valor = parcela.valor - saldo
                    parcela.save()
            return "PARCIAL"

    def _creditar_cc(self, cliente, valor, descricao):
        try:
            cc = ContaCorrente.objects.get(cliente=cliente)
            MovimentacaoConta.objects.create(
                conta=cc, tipo="CREDITO", valor=valor, descricao=descricao,
            )
        except ContaCorrente.DoesNotExist:
            pass

    def _debitar_cc(self, cliente, valor, descricao):
        try:
            cc = ContaCorrente.objects.get(cliente=cliente)
            MovimentacaoConta.objects.create(
                conta=cc, tipo="DEBITO", valor=valor, descricao=descricao,
            )
        except ContaCorrente.DoesNotExist:
            pass
