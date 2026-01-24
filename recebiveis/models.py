from django.db import models
from django.utils import timezone
from clientes.models import Cliente
from django.db.models import Sum
from decimal import Decimal
from .utils import gerar_id_recebivel

class ContratoRecebivel(models.Model):
    STATUS_CHOICES = (
        ('simulado', 'Simulado'),
        ('ativo', 'Ativo'),
        ('renegociado', 'Renegociado'),
        ('liquidado', 'Liquidado'), 
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    contrato_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='simulado')
    taxa_desconto = models.DecimalField(max_digits=5, decimal_places=2, default=0.05)
    valor_bruto = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_liquido = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    data_criacao = models.DateField(default=timezone.now)
    data_ativacao = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.contrato_id:
            self.contrato_id = gerar_id_recebivel(prefixo="REC")
        super().save(*args, **kwargs)

    def calcular_valores(self):
        self.valor_bruto = Decimal('0.00')
        total_desconto = Decimal('0.00')
        taxa_percentual = self.taxa_desconto / Decimal('100')
        
        itens = self.itens.all()
        if not itens:
            self.valor_liquido = Decimal('0.00')
            self.save()
            return

        for item in itens:
            self.valor_bruto += item.valor
            dias = (item.vencimento - self.data_criacao).days
            if dias < 0: dias = 0
            fator_tempo = Decimal(dias) / Decimal('30')
            desconto_item = item.valor * taxa_percentual * fator_tempo
            total_desconto += desconto_item

        self.valor_liquido = self.valor_bruto - total_desconto
        self.save()
        
    def atualizar_status(self):
        """Verifica se todos os itens estão pagos para liquidar o contrato."""
        if self.status == 'simulado':
            return
            
        itens = self.itens.all()
        if itens.exists() and not itens.filter(status='aberto').exists():
            self.status = 'liquidado'
            self.save()

    class Meta:
        app_label = 'recebiveis'
        verbose_name = 'Contrato de Adiantamento de Recebíveis'
        verbose_name_plural = 'Contratos de Adiantamento de Recebíveis'
    
    def __str__(self):
        return f"{self.contrato_id or 'Simulação'} - {self.cliente.nome_completo}"

class ItemRecebivel(models.Model):
    TIPO_CHOICES = (
        ('cheque', 'Cheque'),
        ('nota_fiscal', 'Nota Fiscal'),
        ('cartao', 'Cartão'),
    )
    
    STATUS_ITEM_CHOICES = (
        ('aberto', 'Aberto'),
        ('pago', 'Pago'),
    )

    contrato = models.ForeignKey(ContratoRecebivel, related_name='itens', on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    numero = models.CharField(max_length=50)
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=15, decimal_places=2)
    
    # Novos campos para controle de baixa
    status = models.CharField(max_length=10, choices=STATUS_ITEM_CHOICES, default='aberto')
    data_pagamento = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.contrato.calcular_valores()

    class Meta:
        app_label = 'recebiveis'
        verbose_name = 'Item de Recebível'
        verbose_name_plural = 'Itens de Recebíveis'

    def __str__(self):
        return f"{self.get_tipo_display()} {self.numero} - R$ {self.valor}"