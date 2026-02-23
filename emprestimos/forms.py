from django import forms
from decimal import Decimal
from django.utils import timezone

from .models import Emprestimo, PropostaEmprestimo
from clientes.models import Cliente


class BuscaClienteForm(forms.Form):
    query = forms.CharField(
        label='Buscar Cliente',
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Nome ou CPF...'
        })
    )


class EmprestimoForm(forms.ModelForm):
    # Redefinimos o campo como CharField (Texto) para o Django aceitar o "R$"
    valor_emprestado = forms.CharField(
        label='Valor do Empréstimo',
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg money-mask', 
            'placeholder': 'R$ 0,00'
        })
    )

    class Meta:
        model = Emprestimo
        fields = [
            'parceiro',             # <--- Parceiro
            'percentual_comissao',  # <--- Percentual
            'valor_emprestado', 
            'taxa_juros_mensal', 
            'qtd_parcelas', 
            'primeiro_vencimento',
            'tem_multa_atraso',
            'multa_atraso_percent',
            'juros_mora_mensal_percent',
            'observacoes'
        ]
        widgets = {
            'parceiro': forms.Select(attrs={'class': 'form-select'}), 
            'percentual_comissao': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            
            'taxa_juros_mensal': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'value': '5.00'}),
            'qtd_parcelas': forms.NumberInput(attrs={'class': 'form-control', 'value': '1'}),
            'primeiro_vencimento': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'observacoes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'multa_atraso_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'juros_mora_mensal_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'tem_multa_atraso': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'parceiro': 'Parceiro / Recebedor (Opcional)',
            'percentual_comissao': 'Comissão (%)',
            'taxa_juros_mensal': 'Taxa de Juros (%)',
            'qtd_parcelas': 'Nº de Parcelas',
            'primeiro_vencimento': '1º Vencimento',
            'observacoes': 'Anotações Internas',
            'tem_multa_atraso': 'Cobrar Multa?',
            'multa_atraso_percent': 'Multa (%)',
            'juros_mora_mensal_percent': 'Mora (% a.m.)'
        }

    def clean_valor_emprestado(self):
        valor = self.cleaned_data.get('valor_emprestado')
        
        # Se vier vazio
        if not valor:
            return None
            
        # Limpa a formatação (R$ 1.234,56 -> 1234.56)
        if isinstance(valor, str):
            valor_limpo = valor.replace('R$', '').replace('.', '').replace(',', '.').strip()
            try:
                return Decimal(valor_limpo)
            except:
                raise forms.ValidationError("Valor inválido. Use o formato 0,00")
        
        return valor


class PropostaForm(forms.ModelForm):
    """Formulário para o vendedor/cliente solicitar um empréstimo"""
    
    valor_solicitado = forms.CharField(
        label='Valor Solicitado',
        widget=forms.TextInput(attrs={
            'class': 'form-control money-mask', 
            'placeholder': 'R$ 0,00'
        })
    )

    class Meta:
        model = PropostaEmprestimo
        fields = [
            'cliente', 
            'valor_solicitado', 
            'qtd_parcelas', 
            'primeiro_vencimento', 
            'taxa_juros',
            'observacoes',
            # O vendedor pode indicar o parceiro na criação se quiser,
            # mas geralmente vem automático do cadastro do cliente.
            'parceiro' 
        ]
        widgets = {
            'cliente': forms.Select(attrs={'class': 'form-select select2'}),
            'qtd_parcelas': forms.NumberInput(attrs={'class': 'form-control'}),
            'primeiro_vencimento': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'taxa_juros': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'observacoes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'parceiro': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean_valor_solicitado(self):
        valor = self.cleaned_data.get('valor_solicitado')
        if not valor: return None
        if isinstance(valor, str):
            valor_limpo = valor.replace('R$', '').replace('.', '').replace(',', '.').strip()
            try:
                return Decimal(valor_limpo)
            except:
                raise forms.ValidationError("Valor inválido")
        return valor


class PropostaAnaliseForm(forms.ModelForm):
    """
    Formulário usado na ESTEIRA DE CRÉDITO.
    Permite alterar dados sensíveis e confirmar o parceiro/comissão antes de aprovar.
    """
    class Meta:
        model = PropostaEmprestimo
        fields = [
            'status', 
            'parceiro',             # <--- Editável na análise
            'percentual_comissao',  # <--- Editável na análise
            'valor_solicitado', 
            'qtd_parcelas', 
            'taxa_juros', 
            'primeiro_vencimento', 
            'parecer_analise'
        ]
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'parceiro': forms.Select(attrs={'class': 'form-select'}),
            'percentual_comissao': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            
            'valor_solicitado': forms.NumberInput(attrs={'class': 'form-control'}),
            'qtd_parcelas': forms.NumberInput(attrs={'class': 'form-control'}),
            'taxa_juros': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'primeiro_vencimento': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'parecer_analise': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Justifique a decisão...'}),
        }
        labels = {
            'parceiro': 'Comissionado / Parceiro',
            'percentual_comissao': '% Comissão',
            'valor_solicitado': 'Valor Aprovado', # Texto mudou para refletir que o analista pode editar
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Opcional: Destacar visualmente o parceiro
        if self.instance and self.instance.parceiro:
            self.fields['percentual_comissao'].help_text = f"Padrão do cliente: {self.instance.cliente.percentual_comissao_padrao}%"