from django import forms
from .models import Emprestimo

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
    class Meta:
        model = Emprestimo
        fields = [
            'valor_emprestado', 
            'taxa_juros_mensal', 
            'qtd_parcelas',         # Nome EXATO conforme seu models.py
            'primeiro_vencimento'
        ]
        widgets = {
            'valor_emprestado': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'taxa_juros_mensal': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'qtd_parcelas': forms.NumberInput(attrs={'class': 'form-control'}),
            'primeiro_vencimento': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }
        labels = {
            'valor_emprestado': 'Valor do Empréstimo',
            'taxa_juros_mensal': 'Taxa de Juros (%)',
            'qtd_parcelas': 'Quantidade de Parcelas',
            'primeiro_vencimento': 'Vencimento da 1ª Parcela',
        }