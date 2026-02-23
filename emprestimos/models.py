from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.utils import timezone

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

    # === CAMPOS DE PARCEIRO E COMISSÃO ===
    parceiro = models.ForeignKey(
        Cliente,
        on_delete=models.SET_NULL, # Se excluir o parceiro, o empréstimo continua existindo
        null=True,
        blank=True, # É opcional (nem todo empréstimo tem parceiro)
        related_name="emprestimos_parceiro", # Nome para diferenciar do cliente devedor
        verbose_name="Parceiro / Recebedor"
    )
    
    percentual_comissao = models.DecimalField(
        " % Comissão",
        max_digits=5, 
        decimal_places=2, 
        default=Decimal("10.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))]
    )
    # =====================================

    valor_emprestado = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    qtd_parcelas = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(360)])
    taxa_juros_mensal = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    primeiro_vencimento = models.DateField()

    # valores calculados/salvos
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

    @property
    def dados_atualizados(self):
        """
        Retorna um dicionário com todos os dados calculados.
        """
        hoje = timezone.localdate()
        
        # Se não está atrasada ou já foi paga, retorna o valor normal
        if self.status != ParcelaStatus.ABERTA or self.vencimento >= hoje:
            return {
                'valor_original': self.valor,
                'multa': Decimal("0.00"),
                'juros': Decimal("0.00"),
                'dias_atraso': 0,
                'total': self.valor
            }

        contrato = self.emprestimo
        dias_atraso = (hoje - self.vencimento).days
        
        # 1. Calcular Multa
        multa = Decimal("0.00")
        if contrato.tem_multa_atraso:
            multa = self.valor * (contrato.multa_atraso_percent / Decimal("100"))
        
        # 2. Calcular Juros (Juros Simples pro-rata dia)
        juros = Decimal("0.00")
        if contrato.juros_mora_mensal_percent > 0:
            taxa_diaria = (contrato.juros_mora_mensal_percent / Decimal("30")) / Decimal("100")
            juros = self.valor * taxa_diaria * Decimal(dias_atraso)

        total = self.valor + multa + juros

        return {
            'valor_original': self.valor,
            'multa': multa.quantize(Decimal("0.01")),
            'juros': juros.quantize(Decimal("0.01")),
            'dias_atraso': dias_atraso,
            'total': total.quantize(Decimal("0.01"))
        }

    @property
    def valor_atual(self):
        return self.dados_atualizados['total']

    @transaction.atomic
    def marcar_como_paga(self, valor_pago=None, data_pagamento=None):
        self.status = ParcelaStatus.PAGA
        self.data_pagamento = data_pagamento or timezone.localdate()
        self.valor_pago = valor_pago if valor_pago is not None else self.valor_atual
        self.save(update_fields=["status", "data_pagamento", "valor_pago", "atualizado_em"])

        emp = self.emprestimo
        emp.atualizar_status()
        emp.save(update_fields=["status", "atualizado_em"])


class ContratoLog(models.Model):
    class Acao(models.TextChoices):
        CRIADO = "CRIADO", "Criado"
        PAGO = "PAGO", "Pago"
        RENEGOCIADO = "RENEGOCIADO", "Renegociado"
        CANCELADO = "CANCELADO", "Cancelado"
        REABERTO = "REABERTO", "Reaberto"

    contrato = models.ForeignKey(Emprestimo, on_delete=models.CASCADE, related_name="logs")
    acao = models.CharField(max_length=50, choices=Acao.choices)
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    criado_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=255, null=True, blank=True)
    observacao = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.contrato.codigo_contrato} - {self.acao} em {self.criado_em.strftime('%d/%m/%Y')}"
    


class PropostaEmprestimo(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Aguardando Análise'),
        ('APROVADO', 'Aprovado'),
        ('NEGADO', 'Negado'),
        ('CANCELADO', 'Cancelado'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="propostas")
    valor_solicitado = models.DecimalField(max_digits=12, decimal_places=2)
    qtd_parcelas = models.IntegerField()
    taxa_juros = models.DecimalField(max_digits=6, decimal_places=2)
    primeiro_vencimento = models.DateField()
    
    # === NOVOS CAMPOS NA PROPOSTA ===
    parceiro = models.ForeignKey(
        Cliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="propostas_parceiro",
        verbose_name="Parceiro Indicador"
    )
    
    percentual_comissao = models.DecimalField(
        " % Comissão",
        max_digits=5, 
        decimal_places=2, 
        default=Decimal("10.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))]
    )
    # ================================

    # Dados da Análise
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE')
    data_solicitacao = models.DateTimeField(auto_now_add=True)
    data_analise = models.DateTimeField(null=True, blank=True)
    
    usuario_solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='propostas_criadas', on_delete=models.SET_NULL, null=True)
    usuario_aprovador = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='propostas_analisadas', on_delete=models.SET_NULL, null=True)
    
    observacoes = models.TextField(blank=True, help_text="Observações do vendedor")
    parecer_analise = models.TextField(blank=True, help_text="Justificativa da aprovação/reprovação")

    # Link caso vire empréstimo
    emprestimo_gerado = models.ForeignKey(Emprestimo, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        # Automacao: Se for criação (sem ID) e o cliente tiver parceiro padrão, puxa os dados
        if not self.pk and self.cliente:
            # Só preenche se o campo parceiro ainda estiver vazio na proposta
            if not self.parceiro and hasattr(self.cliente, 'parceiro_padrao') and self.cliente.parceiro_padrao:
                self.parceiro = self.cliente.parceiro_padrao
                # Puxa o percentual do cliente, se houver, senão mantém o default do model (10.00)
                if hasattr(self.cliente, 'percentual_comissao_padrao') and self.cliente.percentual_comissao_padrao is not None:
                    self.percentual_comissao = self.cliente.percentual_comissao_padrao
                    
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Prop. {self.id} - {self.cliente.nome_completo}"