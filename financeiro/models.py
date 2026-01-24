from django.db import models
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth.models import User  # Importação Necessária

class Transacao(models.Model):
    TIPO_CHOICES = [
        ('DEPOSITO', 'Aporte/Depósito'),
        ('SAQUE', 'Sangria/Saque'),
        ('EMPRESTIMO_SAIDA', 'Empréstimo (Saída)'),
        ('PAGAMENTO_ENTRADA', 'Pagamento de Parcela (Entrada)'),
        ('ESTORNO', 'Estorno'),
    ]

    valor = models.DecimalField(max_digits=12, decimal_places=2)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)
    
    # Campos de Vinculação
    transacao_original = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='estornos',
        verbose_name='Transação Original'
    )
    
    emprestimo = models.ForeignKey(
        'emprestimos.Emprestimo', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='transacoes'
    )

    # === NOVOS CAMPOS DE AUDITORIA (CÓDIGO DE AUTENTICAÇÃO) ===
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    ip_origem = models.GenericIPAddressField(null=True, blank=True)
    codigo_autenticacao = models.TextField(blank=True, null=True, help_text="Código hash para auditoria")

    def save(self, *args, **kwargs):
        # Gera o código de autenticação antes de salvar, se ele não existir
        if not self.codigo_autenticacao:
            self.gerar_codigo_auditoria()
        super().save(*args, **kwargs)

    def gerar_codigo_auditoria(self):
        """Gera uma string formatada linha a linha para leitura/auditoria."""
        user_str = self.usuario.username if self.usuario else "Sistema/Desconhecido"
        # Garante que a data tenha timezone ou usa string simples
        data_str = self.data.strftime('%d/%m/%Y às %H:%M:%S')
        ip_str = self.ip_origem if self.ip_origem else "IP Não registrado"
        valor_str = f"R$ {self.valor}"
        
        # Cria um bloco de texto único que serve como "Assinatura Digital" simples
        texto = (
            f"AUTENTICACAO_FINANCEIRA\n"
            f"-----------------------\n"
            f"ID_TRANSACAO: {timezone.now().timestamp()}\n"
            f"TIPO: {self.get_tipo_display()}\n"
            f"VALOR: {valor_str}\n"
            f"USUARIO_RESPONSAVEL: {user_str}\n"
            f"DATA_REGISTRO: {data_str}\n"
            f"IP_ORIGEM: {ip_str}\n"
            f"DESCRICAO: {self.descricao}\n"
            f"-----------------------\n"
            f"HASH_VERIFICACAO: {hash(f'{user_str}{data_str}{valor_str}')}" # Hash simples
        )
        self.codigo_autenticacao = texto

    class Meta:
        ordering = ['-data']
        verbose_name = 'Transação'
        verbose_name_plural = 'Transações'

    def __str__(self):
        return f"{self.get_tipo_display()} - R$ {self.valor}"

def calcular_saldo_atual():
    saldo = Transacao.objects.aggregate(total=models.Sum('valor'))['total']
    return saldo or Decimal('0.00')

class LancamentoFinanceiro(models.Model):
    data = models.DateField()
    descricao = models.CharField(max_length=255)
    debito = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    credito = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    class Meta:
        verbose_name = 'Lançamento Financeiro'
        verbose_name_plural = 'Lançamentos Financeiros'