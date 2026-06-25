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

    @property
    def parcelas_vencidas(self):
        """Quantidade de parcelas em atraso."""
        hoje = timezone.localdate()
        return self.parcelas.filter(
            status=ParcelaStatus.ABERTA, vencimento__lt=hoje
        ).count()


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
        ('CAPTACAO', 'Captação'),
        ('DOCUMENTACAO', 'Análise Documental'),
        ('ANALISE_CREDITO', 'Análise de Crédito'),
        ('COMITE', 'Comitê'),
        ('FORMALIZACAO', 'Formalização'),
        ('LIBERACAO', 'Liberação'),
        ('APROVADO', 'Aprovado'),
        ('NEGADO', 'Negado'),
        ('CANCELADO', 'Cancelado'),
        # Mantém compatibilidade
        ('PENDENTE', 'Pendente (Legado)'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="propostas")
    valor_solicitado = models.DecimalField(max_digits=12, decimal_places=2)
    qtd_parcelas = models.IntegerField()
    taxa_juros = models.DecimalField(max_digits=6, decimal_places=2)
    primeiro_vencimento = models.DateField()

    # Finalidade
    FINALIDADE_CHOICES = [
        ("CREDITO_PESSOAL", "Crédito Pessoal"),
        ("CAPITAL_GIRO", "Capital de Giro"),
        ("FINANCIAMENTO", "Financiamento"),
        ("REFINANCIAMENTO", "Refinanciamento"),
        ("RENEGOCIACAO", "Renegociação"),
        ("OUTRO", "Outro"),
    ]
    finalidade = models.CharField(
        "Finalidade", max_length=20,
        choices=FINALIDADE_CHOICES, default="CREDITO_PESSOAL",
    )

    # IOF
    tem_iof = models.BooleanField("Cobrar IOF?", default=True)
    iof_aliquota = models.DecimalField(
        "Alíquota IOF (%)", max_digits=6, decimal_places=4, default=Decimal("0.0082"),
        help_text="IOF diário padrão: 0,0082% a.d. + 0,38% adicional",
    )
    iof_adicional = models.DecimalField(
        "IOF Adicional (%)", max_digits=5, decimal_places=2, default=Decimal("0.38"),
    )
    valor_iof = models.DecimalField(
        "Valor IOF (R$)", max_digits=12, decimal_places=2, default=Decimal("0.00"),
    )

    # Débitos extras
    valor_debitos_extras = models.DecimalField(
        "Débitos Extras (R$)", max_digits=12, decimal_places=2, default=Decimal("0.00"),
    )
    descricao_debitos = models.CharField(
        "Descrição Débitos Extras", max_length=200, blank=True, default="",
    )

    # Valor bruto (solicitado + IOF + extras)
    valor_bruto = models.DecimalField(
        "Valor Bruto (R$)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Valor total do contrato = solicitado + IOF + extras",
    )

    # Multa e Juros de Mora
    tem_multa = models.BooleanField("Aplicar Multa por Atraso?", default=True)
    multa_percent = models.DecimalField(
        "Multa (%)", max_digits=5, decimal_places=2, default=Decimal("2.00")
    )
    tem_juros_mora = models.BooleanField("Aplicar Juros de Mora?", default=True)
    juros_mora_percent = models.DecimalField(
        "Juros de Mora (% a.m.)", max_digits=5, decimal_places=2, default=Decimal("2.00")
    )
    
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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CAPTACAO')
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

    @property
    def etapa_atual_obj(self):
        """Retorna a etapa ativa mais recente."""
        return self.etapas.filter(ativa=True).order_by('-criado_em').first()

    @property
    def etapa_display(self):
        """Nome amigável da etapa atual."""
        etapa = self.etapa_atual_obj
        if etapa:
            return etapa.get_etapa_display()
        return self.get_status_display()


# ==============================================================================
# ESTEIRA DE APROVAÇÃO — Workflow Multi-Etapa
# ==============================================================================

class EtapaProposta(models.Model):
    """Cada registro representa uma etapa que a proposta percorreu."""

    class Etapa(models.TextChoices):
        CAPTACAO = "CAPTACAO", "Captação"
        DOCUMENTACAO = "DOCUMENTACAO", "Análise Documental"
        ANALISE_CREDITO = "ANALISE_CREDITO", "Análise de Crédito"
        COMITE = "COMITE", "Comitê"
        FORMALIZACAO = "FORMALIZACAO", "Formalização"
        LIBERACAO = "LIBERACAO", "Liberação"

    class Resultado(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        APROVADO = "APROVADO", "Aprovado"
        DEVOLVIDO = "DEVOLVIDO", "Devolvido"
        NEGADO = "NEGADO", "Negado"

    # Ordem das etapas (usado para avançar/voltar)
    ORDEM = {
        "CAPTACAO": 1,
        "DOCUMENTACAO": 2,
        "ANALISE_CREDITO": 3,
        "COMITE": 4,
        "FORMALIZACAO": 5,
        "LIBERACAO": 6,
    }

    # Cargo mínimo necessário para cada etapa
    CARGO_MINIMO = {
        "CAPTACAO": "OPERADOR",
        "DOCUMENTACAO": "OPERADOR",
        "ANALISE_CREDITO": "ANALISTA",
        "COMITE": "GERENTE",
        "FORMALIZACAO": "OPERADOR",
        "LIBERACAO": "GERENTE",
    }

    # SLA em horas para cada etapa
    SLA_HORAS = {
        "CAPTACAO": 4,
        "DOCUMENTACAO": 24,
        "ANALISE_CREDITO": 48,
        "COMITE": 72,
        "FORMALIZACAO": 24,
        "LIBERACAO": 8,
    }

    proposta = models.ForeignKey(
        PropostaEmprestimo,
        on_delete=models.CASCADE,
        related_name="etapas"
    )
    etapa = models.CharField(max_length=20, choices=Etapa.choices)
    resultado = models.CharField(
        max_length=20,
        choices=Resultado.choices,
        default=Resultado.PENDENTE
    )
    ativa = models.BooleanField(default=True)

    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="etapas_responsavel"
    )
    parecer = models.TextField("Parecer / Observações", blank=True, default="")

    criado_em = models.DateTimeField(auto_now_add=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["criado_em"]
        verbose_name = "Etapa da Proposta"
        verbose_name_plural = "Etapas das Propostas"

    def __str__(self):
        return f"Prop. {self.proposta_id} — {self.get_etapa_display()} ({self.get_resultado_display()})"

    @property
    def sla_horas(self):
        return self.SLA_HORAS.get(self.etapa, 24)

    @property
    def sla_estourado(self):
        if self.finalizado_em or not self.ativa:
            return False
        limite = self.criado_em + timezone.timedelta(hours=self.sla_horas)
        return timezone.now() > limite

    @property
    def tempo_restante(self):
        """Retorna timedelta restante do SLA (negativo = estourado)."""
        limite = self.criado_em + timezone.timedelta(hours=self.sla_horas)
        return limite - timezone.now()

    @property
    def cargo_minimo(self):
        return self.CARGO_MINIMO.get(self.etapa, "OPERADOR")

    @property
    def ordem(self):
        return self.ORDEM.get(self.etapa, 0)


class ChecklistItem(models.Model):
    """Itens que precisam ser verificados em cada etapa."""

    etapa_proposta = models.ForeignKey(
        EtapaProposta,
        on_delete=models.CASCADE,
        related_name="checklist"
    )
    descricao = models.CharField("Descrição", max_length=200)
    obrigatorio = models.BooleanField("Obrigatório?", default=True)
    concluido = models.BooleanField("Concluído?", default=False)
    concluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    concluido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-obrigatorio", "descricao"]
        verbose_name = "Item do Checklist"
        verbose_name_plural = "Itens do Checklist"

    def __str__(self):
        status = "✓" if self.concluido else "○"
        return f"{status} {self.descricao}"


class PoliticaCredito(models.Model):
    """Regras de crédito configuráveis pela empresa."""

    nome = models.CharField("Nome da Política", max_length=100, default="Padrão")
    ativo = models.BooleanField(default=True)

    # Limites de valor
    valor_minimo = models.DecimalField(
        "Valor Mínimo", max_digits=12, decimal_places=2, default=Decimal("500.00")
    )
    valor_maximo = models.DecimalField(
        "Valor Máximo", max_digits=12, decimal_places=2, default=Decimal("100000.00")
    )

    # Limites de prazo
    prazo_maximo_meses = models.IntegerField("Prazo Máximo (meses)", default=36)

    # Taxas
    taxa_minima = models.DecimalField(
        "Taxa Mínima (%)", max_digits=5, decimal_places=2, default=Decimal("1.00")
    )
    taxa_maxima = models.DecimalField(
        "Taxa Máxima (%)", max_digits=5, decimal_places=2, default=Decimal("15.00")
    )

    # Regras de risco
    score_minimo_aprovacao = models.CharField(
        "Score Mínimo",
        max_length=30,
        default="Neutro",
        help_text="Bom Pagador, Neutro, Risco Médio, etc."
    )
    max_contratos_ativos = models.IntegerField(
        "Máx. Contratos Ativos Simultâneos", default=3
    )
    permite_inadimplente = models.BooleanField(
        "Permitir proposta de inadimplente?", default=False
    )

    # Alçada — valor máximo sem comitê
    valor_max_sem_comite = models.DecimalField(
        "Valor Máx. sem Comitê",
        max_digits=12, decimal_places=2,
        default=Decimal("10000.00"),
        help_text="Acima deste valor, a proposta vai automaticamente pro Comitê."
    )

    class Meta:
        verbose_name = "Política de Crédito"
        verbose_name_plural = "Políticas de Crédito"

    def __str__(self):
        return f"{self.nome} ({'Ativa' if self.ativo else 'Inativa'})"


class VotoComite(models.Model):
    """Registro de voto de cada membro do comitê."""

    DECISAO_CHOICES = [
        ("DEFERIDO", "Deferido"),
        ("INDEFERIDO", "Indeferido"),
    ]

    proposta = models.ForeignKey(
        PropostaEmprestimo, on_delete=models.CASCADE, related_name="votos_comite"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="votos_comite"
    )
    decisao = models.CharField("Decisão", max_length=15, choices=DECISAO_CHOICES)
    observacoes = models.TextField(
        "Observações",
        blank=True,
        default="",
        help_text="Ressalvas aceitas pelo votante (ex: comprovante de renda desatualizado)",
    )
    data_voto = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_voto"]
        verbose_name = "Voto do Comitê"
        verbose_name_plural = "Votos do Comitê"
        unique_together = [("proposta", "usuario")]

    def __str__(self):
        return f"Voto {self.get_decisao_display()} — {self.usuario} — Prop. {self.proposta_id}"


class ContratoFormalizado(models.Model):
    """Controle de emissão de contrato e nota promissória na formalização."""

    proposta = models.OneToOneField(
        PropostaEmprestimo, on_delete=models.CASCADE, related_name="contrato_formal"
    )
    numero = models.IntegerField("Número Sequencial")
    ano = models.IntegerField("Ano")
    numero_formatado = models.CharField("Nº Contrato", max_length=20, unique=True)

    contrato_emitido = models.BooleanField("Contrato Emitido", default=False)
    contrato_emitido_em = models.DateTimeField(null=True, blank=True)

    promissoria_emitida = models.BooleanField("Nota Promissória Emitida", default=False)
    promissoria_emitida_em = models.DateTimeField(null=True, blank=True)

    assinado_cliente = models.BooleanField("Assinado pelo Cliente", default=False)
    assinado_empresa = models.BooleanField("Assinado pela Empresa", default=False)

    emitido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ano", "-numero"]
        verbose_name = "Contrato Formalizado"
        verbose_name_plural = "Contratos Formalizados"

    def __str__(self):
        return f"Contrato {self.numero_formatado} — Proposta #{self.proposta_id}"

    @classmethod
    def proximo_numero(cls, ano=None):
        if ano is None:
            ano = timezone.localdate().year
        ultimo = cls.objects.filter(ano=ano).order_by("-numero").first()
        return (ultimo.numero + 1) if ultimo else 1

    @classmethod
    def gerar_numero_formatado(cls, numero, ano):
        return f"{numero:03d}/{ano} EMP"


class GarantiaProposta(models.Model):
    """Garantias vinculadas a uma proposta de empréstimo."""

    TIPO_CHOICES = [
        ("CHEQUE", "Cheque"),
        ("AVALISTA", "Avalista"),
        ("BEM_MOVEL", "Bem Móvel (Veículo)"),
        ("BEM_IMOVEL", "Bem Imóvel"),
    ]

    proposta = models.ForeignKey(
        PropostaEmprestimo, on_delete=models.CASCADE, related_name="garantias"
    )
    tipo = models.CharField("Tipo", max_length=15, choices=TIPO_CHOICES)
    descricao = models.CharField("Descrição", max_length=200, blank=True, default="")

    # Cheque
    cheque_banco = models.CharField("Banco", max_length=50, blank=True, default="")
    cheque_numero = models.CharField("Nº Cheque", max_length=30, blank=True, default="")
    cheque_valor = models.DecimalField("Valor Cheque", max_digits=12, decimal_places=2, null=True, blank=True)

    # Avalista (outro cliente)
    avalista = models.ForeignKey(
        Cliente, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="garantias_avalista", verbose_name="Avalista",
    )

    # Bem móvel do cliente
    bem_movel = models.ForeignKey(
        "clientes.BemMovel", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="garantias_proposta",
    )

    # Bem imóvel do cliente
    bem_imovel = models.ForeignKey(
        "clientes.BemImovel", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="garantias_proposta",
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tipo"]
        verbose_name = "Garantia da Proposta"
        verbose_name_plural = "Garantias das Propostas"

    def __str__(self):
        if self.tipo == "CHEQUE":
            return f"Cheque {self.cheque_numero} — R$ {self.cheque_valor or 0}"
        if self.tipo == "AVALISTA" and self.avalista:
            return f"Avalista: {self.avalista.nome_completo}"
        if self.tipo == "BEM_MOVEL" and self.bem_movel:
            return f"Veículo: {self.bem_movel.placa} — {self.bem_movel.descricao}"
        if self.tipo == "BEM_IMOVEL" and self.bem_imovel:
            return f"Imóvel: {self.bem_imovel.get_tipo_display()} — {self.bem_imovel.matricula}"
        return f"{self.get_tipo_display()}: {self.descricao}"