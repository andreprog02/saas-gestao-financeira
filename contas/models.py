from django.db import models
from django.utils import timezone
from decimal import Decimal
from clientes.models import Cliente
from emprestimos.models import Emprestimo, Parcela


class ContaCorrente(models.Model):
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='conta_corrente')
    saldo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    saldo_bloqueado = models.DecimalField(
        "Saldo Bloqueado", max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text="Cheques em compensação — não disponível para saque",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Conta de {self.cliente.nome_completo} - Disp: R$ {self.saldo_disponivel} (Bloq: R$ {self.saldo_bloqueado})"

    @property
    def saldo_disponivel(self):
        return self.saldo

    @property
    def saldo_total(self):
        return self.saldo + self.saldo_bloqueado

    def recalcular_saldo(self):
        """Recalcula saldo a partir das movimentações."""
        from django.db.models import Sum
        creditos = self.movimentacoes.filter(tipo="CREDITO").aggregate(s=Sum("valor"))["s"] or Decimal("0")
        debitos = self.movimentacoes.filter(tipo="DEBITO").aggregate(s=Sum("valor"))["s"] or Decimal("0")
        bloqueados = self.movimentacoes.filter(tipo="CREDITO_BLOQUEADO", estornado=False).aggregate(s=Sum("valor"))["s"] or Decimal("0")
        desbloqueados = self.movimentacoes.filter(tipo="DESBLOQUEIO").aggregate(s=Sum("valor"))["s"] or Decimal("0")
        self.saldo = creditos - debitos
        self.saldo_bloqueado = bloqueados - desbloqueados
        self.save(update_fields=["saldo", "saldo_bloqueado"])


class MovimentacaoConta(models.Model):
    TIPO_CHOICES = [
        ('CREDITO', 'Crédito (Entrada)'),
        ('DEBITO', 'Débito (Saída)'),
        ('CREDITO_BLOQUEADO', 'Crédito Bloqueado (Cheque em Compensação)'),
        ('DESBLOQUEIO', 'Desbloqueio (Cheque Compensado)'),
    ]

    ORIGEM_CHOICES = [
        ('EMPRESTIMO', 'Empréstimo Aprovado'),
        ('SAQUE', 'Saque em Espécie'),
        ('DEPOSITO', 'Depósito em Espécie'),
        ('PAGAMENTO_PARCELA', 'Pagamento de Parcela'),
        ('TAXA', 'Cobrança de Taxa'),
        ('ANTECIPACAO', 'Antecipação de Recebíveis'),
        ('COMISSAO', 'Comissão de Parceiro'),
        ('CHEQUE_COMPENSACAO', 'Cheque em Compensação'),
        ('CHEQUE_COMPENSADO', 'Cheque Compensado'),
        ('CHEQUE_DEVOLVIDO', 'Cheque Devolvido (Estorno)'),
    ]

    ALINEA_CHOICES = [
        ('', 'Nenhuma'),
        ('11', '11 - Sem fundos (1ª apresentação)'),
        ('12', '12 - Sem fundos (2ª apresentação)'),
        ('13', '13 - Conta encerrada'),
        ('20', '20 - Folha de cheque cancelada'),
        ('21', '21 - Contra-ordem / Sustação'),
        ('22', '22 - Divergência de assinatura'),
        ('25', '25 - Sem motivo de devolução'),
        ('28', '28 - Sustação judicial'),
        ('29', '29 - Falta de confirmação de recebimento'),
        ('31', '31 - Erro formal'),
        ('33', '33 - Divergência de endosso'),
        ('34', '34 - Cheque apresentado por estabelecimento não conveniado'),
        ('35', '35 - Cheque fraudado'),
        ('44', '44 - Cheque prescrito (+ de 6 meses)'),
        ('48', '48 - Cheque de valor superior ao estabelecido'),
        ('49', '49 - Remessa nula'),
    ]

    conta = models.ForeignKey(ContaCorrente, on_delete=models.CASCADE, related_name='movimentacoes')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    origem = models.CharField(max_length=20, choices=ORIGEM_CHOICES, default='SAQUE')
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)

    # Cheque vinculado
    cheque_custodia = models.ForeignKey(
        'financeiro.ChequeCustodia', on_delete=models.SET_NULL, null=True, blank=True,
    )
    alinea = models.CharField("Alínea Devolução", max_length=5, choices=ALINEA_CHOICES, blank=True, default="")

    # Estorno
    estornado = models.BooleanField(default=False)
    mov_estorno = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)

    # Vínculos
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.SET_NULL, null=True, blank=True)
    parcela = models.ForeignKey(Parcela, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-data"]

    def __str__(self):
        est = " [EST]" if self.estornado else ""
        return f"{self.get_tipo_display()} — R$ {self.valor} — {self.descricao}{est}"

    def save(self, *args, **kwargs):
        if not self.pk:
            saldo_atual = Decimal(str(self.conta.saldo))
            bloq_atual = Decimal(str(self.conta.saldo_bloqueado))
            valor = Decimal(str(self.valor))

            if self.tipo == 'CREDITO':
                self.conta.saldo = saldo_atual + valor
            elif self.tipo == 'DEBITO':
                self.conta.saldo = saldo_atual - valor
            elif self.tipo == 'CREDITO_BLOQUEADO':
                self.conta.saldo_bloqueado = bloq_atual + valor
            elif self.tipo == 'DESBLOQUEIO':
                self.conta.saldo_bloqueado = max(Decimal("0"), bloq_atual - valor)
                self.conta.saldo = saldo_atual + valor

            self.conta.save()

        super().save(*args, **kwargs)
