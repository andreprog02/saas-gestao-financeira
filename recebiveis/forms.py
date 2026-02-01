from django import forms
from .models import ContratoRecebivel, ItemRecebivel

class ContratoRecebivelForm(forms.ModelForm):
    class Meta:
        model = ContratoRecebivel
        fields = ['cliente', 'taxa_desconto']

class ItemRecebivelForm(forms.ModelForm):
    # Campo de valor como texto para tratar a máscara manualmente
    valor = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))

    class Meta:
        model = ItemRecebivel
        fields = ['tipo', 'numero', 'vencimento', 'valor']
        widgets = {
            # type="date" envia YYYY-MM-DD
            'vencimento': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Aceita formatos ISO (do input date) e BR
        self.fields['vencimento'].input_formats = ['%Y-%m-%d', '%d/%m/%Y']

    def clean_valor(self):
        valor_str = self.cleaned_data['valor']
        # Se vier como string, trata a formatação brasileira
        if isinstance(valor_str, str):
            valor_str = valor_str.replace('.', '').replace(',', '.')
        return valor_str

class AtivacaoForm(forms.Form):
    senha = forms.CharField(widget=forms.PasswordInput, label='Senha para Ativação')
    saque_inicial = forms.DecimalField(
        label='Saque Inicial (Opcional)', 
        required=False, 
        initial=0.00,
        min_value=0.00,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text="Valor a ser retirado em dinheiro/transferência imediatamente. Se R$ 0,00, o valor líquido fica integralmente na Conta Corrente do cliente."
    )

class RenegociacaoForm(forms.ModelForm):
    class Meta:
        model = ContratoRecebivel
        fields = ['taxa_desconto']