from django.db import models
from django.utils import timezone
from decimal import Decimal
from clientes.models import Cliente
from emprestimos.models import Emprestimo, Parcela

class ContaCorrente(models.Model):
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='conta_corrente')
    # O default deve ser Decimal('0.00') para garantir que o saldo comece com o tipo correto
    saldo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Conta de {self.cliente.nome_completo} - R$ {self.saldo}"

class MovimentacaoConta(models.Model):
    TIPO_CHOICES = [
        ('CREDITO', 'Crédito (Entrada)'),
        ('DEBITO', 'Débito (Saída)'),
    ]
    
    ORIGEM_CHOICES = [
        ('EMPRESTIMO', 'Empréstimo Aprovado'),
        ('SAQUE', 'Saque em Espécie (Cód 05)'),
        ('DEPOSITO', 'Depósito em Espécie'),
        ('PAGAMENTO_PARCELA', 'Pagamento de Parcela'),
        ('TAXA', 'Cobrança de Taxa'),
        ('ANTECIPACAO', 'Antecipação de Recebíveis'),
        ('COMISSAO', 'Comissão de Parceiro'),
    ]

    conta = models.ForeignKey(ContaCorrente, on_delete=models.CASCADE, related_name='movimentacoes')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    origem = models.CharField(max_length=20, choices=ORIGEM_CHOICES, default='SAQUE')
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)

    # Vínculos opcionais para rastreabilidade (Saber de qual contrato veio o dinheiro)
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.SET_NULL, null=True, blank=True)
    parcela = models.ForeignKey(Parcela, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        # Atualiza o saldo da conta AUTOMATICAMENTE ao salvar uma nova movimentação
        if not self.pk:  # Apenas na criação (insert)
            
            # CORREÇÃO CRÍTICA: Convertemos explicitamente para Decimal antes da conta
            # Isso evita o erro "unsupported operand type(s) for +=: 'float' and 'decimal.Decimal'"
            saldo_atual = Decimal(str(self.conta.saldo))
            valor_movimento = Decimal(str(self.valor))

            if self.tipo == 'CREDITO':
                self.conta.saldo = saldo_atual + valor_movimento
            else:
                self.conta.saldo = saldo_atual - valor_movimento
            
            self.conta.save()
            
        super().save(*args, **kwargs)