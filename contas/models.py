from django.db import models
from django.utils import timezone
from decimal import Decimal  # Importação Essencial
from clientes.models import Cliente
from emprestimos.models import Emprestimo, Parcela

class ContaCorrente(models.Model):
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='conta_corrente')
    # Alterado default para Decimal('0.00') para garantir o tipo correto desde o início
    saldo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Conta de {self.cliente.nome_completo} - R$ {self.saldo}"

class MovimentacaoConta(models.Model):
    TIPO_CHOICES = [
        ('CREDITO', 'Crédito'),
        ('DEBITO', 'Débito'),
    ]
    ORIGEM_CHOICES = [
        ('EMPRESTIMO', 'Empréstimo (Entrada)'),
        ('SAQUE', 'Saque/Transferência'),
        ('TAXA', 'Taxas/Impostos'),
        ('PAGAMENTO_PARCELA', 'Pagamento Automático'),
        ('DEPOSITO', 'Depósito'),
        ('ANTECIPACAO', 'Antecipação de Recebíveis'),
    ]

    conta = models.ForeignKey(ContaCorrente, on_delete=models.CASCADE, related_name='movimentacoes')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    origem = models.CharField(max_length=20, choices=ORIGEM_CHOICES, default='SAQUE')
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)

    # Vínculos opcionais
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.SET_NULL, null=True, blank=True)
    parcela = models.ForeignKey(Parcela, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        # Atualiza o saldo da conta ao salvar uma movimentação
        if not self.pk:  # Apenas na criação
            # CORREÇÃO DO ERRO: Converte explicitamente o saldo para Decimal antes da conta
            saldo_atual = Decimal(str(self.conta.saldo))
            
            if self.tipo == 'CREDITO':
                saldo_atual += self.valor
            else:
                saldo_atual -= self.valor
            
            self.conta.saldo = saldo_atual
            self.conta.save()
        super().save(*args, **kwargs)