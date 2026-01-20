from django import forms
from .models import ContratoRecebivel, ItemRecebivel

class ContratoRecebivelForm(forms.ModelForm):
    class Meta:
        model = ContratoRecebivel
        fields = ['cliente', 'taxa_desconto']

class ItemRecebivelForm(forms.ModelForm):
    class Meta:
        model = ItemRecebivel
        fields = ['tipo', 'numero', 'vencimento', 'valor']

class AtivacaoForm(forms.Form):
    senha = forms.CharField(widget=forms.PasswordInput, label='Senha para Ativação')

class RenegociacaoForm(forms.ModelForm):
    class Meta:
        model = ContratoRecebivel
        fields = ['taxa_desconto']  # Pode adicionar mais campos se necessário