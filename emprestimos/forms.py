from django import forms
from django.core.validators import MinValueValidator
from decimal import Decimal
from clientes.models import Cliente

class SelecionarClienteForm(forms.Form):
    q = forms.CharField(
        label="Buscar cliente (Nome ou CPF)",
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: André ou 123.456.789-00"}),
    )

class NovoEmprestimoForm(forms.Form):
    cliente_id = forms.IntegerField(widget=forms.HiddenInput())

    valor_emprestado = forms.DecimalField(
        label="Valor emprestado",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )

    qtd_parcelas = forms.IntegerField(
        label="Quantidade de parcelas",
        min_value=1,
        max_value=360,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )

    taxa_juros_mensal = forms.DecimalField(
        label="Taxa de juros (% ao mês)",
        max_digits=6,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )

    primeiro_vencimento = forms.DateField(
        label="Data do 1º vencimento",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )

    # === CAMPO DE SAQUE (Sem IOF) ===
    saque_inicial = forms.DecimalField(
        label='Saque/Transferência Imediata (R$)', 
        required=False, 
        initial=Decimal('0.00'),
        min_value=Decimal('0.00'),
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text="Valor retirado fisicamente do caixa. Se R$ 0,00, o valor fica guardado na conta do cliente."
    )
    # ================================

    # Regras de atraso
    tem_multa_atraso = forms.BooleanField(
        label="Cobrar multa por atraso?",
        required=False, 
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    multa_atraso_percent = forms.DecimalField(
        label="Multa (%)",
        required=False, 
        initial=Decimal("2.00"),
        min_value=Decimal("0.00"), 
        decimal_places=2, 
        max_digits=5,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    
    juros_mora_mensal_percent = forms.DecimalField(
        label="Juros de Mora (% ao mês)",
        required=False, 
        initial=Decimal("1.00"),
        min_value=Decimal("0.00"), 
        decimal_places=2, 
        max_digits=5,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )

    observacoes = forms.CharField(
        label="Observações",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def clean_cliente_id(self):
        cid = self.cleaned_data["cliente_id"]
        if not Cliente.objects.filter(id=cid).exists():
            raise forms.ValidationError("Cliente inválido.")
        return cid

    def clean(self):
        cleaned = super().clean()
        tem_multa = cleaned.get("tem_multa_atraso", False)
        multa = cleaned.get("multa_atraso_percent")
        
        if not tem_multa:
            cleaned["multa_atraso_percent"] = Decimal("0.00")
        else:
            if multa is None:
                cleaned["multa_atraso_percent"] = Decimal("2.00")

        if cleaned.get("juros_mora_mensal_percent") is None:
            cleaned["juros_mora_mensal_percent"] = Decimal("1.00")
            
        # Garante que o saque não seja None
        if cleaned.get("saque_inicial") is None:
            cleaned["saque_inicial"] = Decimal("0.00")

        return cleaned