

# Create your models here.
from django.db import models
from django.utils import timezone
from django.conf import settings
from clientes.models import Cliente
from emprestimos.models import Emprestimo
from recebiveis.models import ContratoRecebivel

class HistoricoCobranca(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    
    # Vínculos opcionais (pode ser de um empréstimo ou de um recebível)
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.CASCADE, null=True, blank=True, related_name='historico_cobranca')
    recebivel = models.ForeignKey(ContratoRecebivel, on_delete=models.CASCADE, null=True, blank=True, related_name='historico_cobranca')
    
    data_evento = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    descricao = models.TextField(verbose_name="Descrição do Evento")
    
    # Campo auxiliar para facilitar a exibição
    tipo_contrato = models.CharField(max_length=20, default="GERAL") 

    class Meta:
        ordering = ['-data_evento']
        verbose_name = 'Histórico de Cobrança'
        verbose_name_plural = 'Históricos de Cobrança'

    def __str__(self):
        return f"{self.cliente} - {self.data_evento.strftime('%d/%m/%Y')}"
    


class CarteiraCobranca(models.Model):
    cliente_devedor = models.OneToOneField(
        Cliente, 
        on_delete=models.CASCADE, 
        related_name='regra_cobranca',
        verbose_name="Cliente Devedor"
    )
    profissional = models.ForeignKey(
        Cliente, 
        on_delete=models.PROTECT, 
        related_name='carteira_profissional',
        verbose_name="Advogado/Cobrador"
    )
    percentual_comissao = models.DecimalField(
        "Percentual (%)",
        max_digits=5, 
        decimal_places=2,
        help_text="Ex: Digite 20.00 para 20%"
    )
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.cliente_devedor} -> {self.profissional} ({self.percentual_comissao}%)"

    class Meta:
        verbose_name = "Regra de Split/Honorários"
        verbose_name_plural = "Regras de Split/Honorários"


class CartaCobranca(models.Model):
    """Carta de cobrança emitida para cliente inadimplente."""

    numero = models.IntegerField("Número Sequencial")
    ano = models.IntegerField("Ano de Emissão")
    numero_formatado = models.CharField("Nº Correspondência", max_length=10, unique=True)

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="cartas_cobranca")
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.CASCADE, related_name="cartas_cobranca")

    qtd_parcelas_atraso = models.IntegerField("Parcelas em Atraso")
    valor_total_atraso = models.DecimalField("Valor Total em Atraso", max_digits=12, decimal_places=2)

    local_emissao = models.CharField("Local", max_length=100, default="Rio de Janeiro")
    data_emissao = models.DateField("Data de Emissão", default=timezone.now)

    emitido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ano", "-numero"]
        verbose_name = "Carta de Cobrança"
        verbose_name_plural = "Cartas de Cobrança"

    def __str__(self):
        return f"Carta {self.numero_formatado} — {self.cliente.nome_completo}"

    @classmethod
    def proximo_numero(cls, ano=None):
        """Retorna o próximo número sequencial para o ano."""
        if ano is None:
            ano = timezone.localdate().year
        ultimo = cls.objects.filter(ano=ano).order_by("-numero").first()
        return (ultimo.numero + 1) if ultimo else 1

    @classmethod
    def gerar_numero_formatado(cls, numero, ano):
        """Formata: 001/2026"""
        return f"{numero:03d}/{ano}"

class DespesaCobranca(models.Model):
    """Despesas de cobrança vinculadas a contratos (cartório, correios, etc.)."""

    TIPO_CHOICES = [
        ("CARTORIO", "Cartório / Protesto"),
        ("CORREIOS", "Correios / Correspondência"),
        ("HONORARIOS", "Honorários Advocatícios"),
        ("CUSTAS", "Custas Judiciais"),
        ("OFICIAL", "Oficial de Justiça"),
        ("DESLOCAMENTO", "Deslocamento"),
        ("CONSULTA_CREDITO", "Consulta de Crédito (SPC/Serasa)"),
        ("LIGACAO", "Ligações Telefônicas"),
        ("OUTROS", "Outros"),
    ]

    emprestimo = models.ForeignKey(
        Emprestimo, on_delete=models.CASCADE, related_name="despesas_cobranca",
        verbose_name="Contrato",
    )
    tipo = models.CharField("Tipo", max_length=20, choices=TIPO_CHOICES)
    descricao = models.CharField("Descrição", max_length=200, blank=True, default="")
    valor = models.DecimalField("Valor (R$)", max_digits=12, decimal_places=2)
    data = models.DateField("Data da Despesa", default=timezone.now)
    comprovante = models.FileField(
        "Comprovante", upload_to="despesas_cobranca/%Y/%m/",
        blank=True, null=True,
    )

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data"]
        verbose_name = "Despesa de Cobrança"
        verbose_name_plural = "Despesas de Cobrança"

    def __str__(self):
        return f"{self.get_tipo_display()} — R$ {self.valor} — {self.emprestimo.codigo_contrato}"
