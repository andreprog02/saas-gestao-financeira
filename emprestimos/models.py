from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator

from clientes.models import Cliente


class EmprestimoStatus(models.TextChoices):
    ATIVO = "ATIVO", "Ativo"
    ATRASADO = "ATRASADO", "Atrasado"
    QUITADO = "QUITADO", "Quitado"
    RENEGOCIADO = "RENEGOCIADO", "Renegociado"
    CANCELADO = "CANCELADO", "Cancelado"


class ParcelaStatus(models.TextChoices):
    ABERTA = "ABERTA", "Aberta"
    PAGA = "PAGA", "Paga"
    LIQUIDADA_RENEGOCIACAO = "LIQUIDADA_RENEGOCIACAO", "Liquidada por renegociação"
    CANCELADA = "CANCELADA", "Cancelada"


class Emprestimo(models.Model):
    contrato_origem = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="aditivos"
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="emprestimos")
    codigo_contrato = models.CharField(max_length=20, unique=True, db_index=True)

    valor_emprestado = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    qtd_parcelas = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(360)])
    taxa_juros_mensal = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    primeiro_vencimento = models.DateField()

    # valores calculados/salvos (já com arredondamento aplicado)
    valor_parcela_aplicada = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_contrato = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_juros = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    ajuste_arredondamento = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    status = models.CharField(max_length=20, choices=EmprestimoStatus.choices, default=EmprestimoStatus.ATIVO)
    observacoes = models.TextField(blank=True, default="")

    criado_em = models.DateTimeField(default=timezone.now, editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    tem_multa_atraso = models.BooleanField(default=True)

    multa_atraso_percent = models.DecimalField(
    max_digits=5, decimal_places=2, default=Decimal("2.00"),
    validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("20.00"))]
    )

    juros_mora_mensal_percent = models.DecimalField(
    max_digits=5, decimal_places=2, default=Decimal("1.00"),
    validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("20.00"))]
    )

    # cancelamento (auditável)
    cancelado_em = models.DateTimeField(null=True, blank=True)
    cancelado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contratos_cancelados"
    )
    motivo_cancelamento = models.CharField(max_length=120, null=True, blank=True)
    observacao_cancelamento = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["codigo_contrato"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.codigo_contrato} - {self.cliente.nome_completo}"

    def atualizar_status(self):
        """
        Atualiza status automaticamente:
        - CANCELADO mantém cancelado
        - se não houver parcelas: ATIVO
        - se não houver parcelas ABERTAS: QUITADO
        - se houver aberta vencida: ATRASADO
        - caso contrário: ATIVO
        """
        if self.status == EmprestimoStatus.CANCELADO:
            return

        hoje = timezone.localdate()
        qs = self.parcelas.all()

        if not qs.exists():
            self.status = EmprestimoStatus.ATIVO
            return

        abertas = qs.filter(status=ParcelaStatus.ABERTA)
        if not abertas.exists():
            self.status = EmprestimoStatus.QUITADO
            return

        vencidas = abertas.filter(vencimento__lt=hoje)
        self.status = EmprestimoStatus.ATRASADO if vencidas.exists() else EmprestimoStatus.ATIVO


class Parcela(models.Model):
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.CASCADE, related_name="parcelas")
    numero = models.PositiveIntegerField()
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(max_length=40, choices=ParcelaStatus.choices, default=ParcelaStatus.ABERTA)

    data_pagamento = models.DateField(null=True, blank=True)
    valor_pago = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    criado_em = models.DateTimeField(default=timezone.now, editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vencimento", "numero"]
        unique_together = [("emprestimo", "numero")]
        indexes = [
            models.Index(fields=["status", "vencimento"]),
        ]

    def __str__(self):
        return f"{self.emprestimo.codigo_contrato} - Parcela {self.numero}"

    @transaction.atomic
    def marcar_como_paga(self, valor_pago=None, data_pagamento=None):
        self.status = ParcelaStatus.PAGA
        self.data_pagamento = data_pagamento or timezone.localdate()
        self.valor_pago = valor_pago if valor_pago is not None else self.valor
        self.save(update_fields=["status", "data_pagamento", "valor_pago", "atualizado_em"])

        emp = self.emprestimo
        emp.atualizar_status()
        emp.save(update_fields=["status", "atualizado_em"])


class ContratoLog(models.Model):
    class Acao(models.TextChoices):
        CRIADO = "CRIADO", "Criado"
        PAGO = "PAGO", "Pagamento"
        RENEGOCIADO = "RENEGOCIADO", "Renegociado"
        CANCELADO = "CANCELADO", "Cancelado"
        REABERTO = "REABERTO", "Reaberto"

    contrato = models.ForeignKey(Emprestimo, on_delete=models.CASCADE, related_name="logs")
    acao = models.CharField(max_length=20, choices=Acao.choices)
    criado_em = models.DateTimeField(default=timezone.now)

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    motivo = models.CharField(max_length=120, null=True, blank=True)
    observacao = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.contrato.codigo_contrato} - {self.acao} - {self.criado_em:%d/%m/%Y %H:%M}"
