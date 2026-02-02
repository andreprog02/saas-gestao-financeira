from django import forms
from decimal import Decimal
from .models import ContratoRecebivel, ItemRecebivel

# === Função auxiliar para limpar valores monetários/porcentagem ===
def limpar_valor_formatado(valor_str):
    if not valor_str:
        return Decimal('0.00')
    
    if isinstance(valor_str, str):
        # Se tiver vírgula, assumimos formato BR (com ou sem R$/%)
        if ',' in valor_str:
            # Remove R$, %, espaços e pontos de milhar
            # Ex: "R$ 1.500,00" vira "1500,00" depois "1500.00"
            limpo = valor_str.replace('R$', '').replace('%', '').replace(' ', '').replace('.', '')
            # Troca vírgula decimal por ponto
            limpo = limpo.replace(',', '.')
            return Decimal(limpo)
        else:
            # Se não tem vírgula, assume formato US/Limpo (ex: 1000.00 ou 1000)
            limpo = valor_str.replace('R$', '').replace('%', '').replace(' ', '')
            return Decimal(limpo)
    
    return valor_str

class ContratoRecebivelForm(forms.ModelForm):
    # Sobrescrevemos para CharField para aceitar "5,00%"
    taxa_desconto = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control percent-mask'}),
        initial="5,00"
    )

    class Meta:
        model = ContratoRecebivel
        fields = ['cliente', 'taxa_desconto']

    def clean_taxa_desconto(self):
        return limpar_valor_formatado(self.cleaned_data['taxa_desconto'])

class ItemRecebivelForm(forms.ModelForm):
    # Define o campo como texto para a máscara funcionar (R$ ...)
    valor = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control money-mask'}))

    class Meta:
        model = ItemRecebivel
        fields = ['tipo', 'numero', 'vencimento', 'valor']
        widgets = {
            'vencimento': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'numero': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: 000123'}),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vencimento'].input_formats = ['%Y-%m-%d', '%d/%m/%Y']

    def clean_valor(self):
        return limpar_valor_formatado(self.cleaned_data['valor'])

class AtivacaoForm(forms.Form):
    senha = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}), 
        label='Senha para Ativação'
    )
    
    saque_inicial = forms.CharField(
        label='Saque Inicial (Opcional)', 
        required=False, 
        initial="0,00",
        widget=forms.TextInput(attrs={'class': 'form-control money-mask'}),
        help_text="Valor a ser retirado em dinheiro imediatamente."
    )

    def clean_saque_inicial(self):
        valor = self.cleaned_data.get('saque_inicial')
        # Se vazio, retorna 0
        if not valor or str(valor).strip() == '':
            return Decimal('0.00')
        return limpar_valor_formatado(valor)

class RenegociacaoForm(forms.ModelForm):
    # Mantido para compatibilidade com views.py
    taxa_desconto = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control percent-mask'}),
        initial="5,00"
    )
    class Meta:
        model = ContratoRecebivel
        fields = ['taxa_desconto']

    def clean_taxa_desconto(self):
        return limpar_valor_formatado(self.cleaned_data['taxa_desconto'])