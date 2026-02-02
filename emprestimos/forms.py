from django import forms
from .models import Emprestimo
from decimal import Decimal
from django.db import models
from django.utils import timezone


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
    # --- CORREÇÃO PRINCIPAL ---
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
            'taxa_juros_mensal': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'value': '5.00'}),
            'qtd_parcelas': forms.NumberInput(attrs={'class': 'form-control', 'value': '1'}),
            'primeiro_vencimento': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'observacoes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'multa_atraso_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'juros_mora_mensal_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'tem_multa_atraso': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
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
    
