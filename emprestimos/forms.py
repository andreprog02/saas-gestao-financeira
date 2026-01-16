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


class NovoEmprestimoForm(forms.Form):
    valor_emprestado = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2, max_digits=12)
    qtd_parcelas = forms.IntegerField(min_value=1, max_value=360)
    taxa_juros_mensal = forms.DecimalField(min_value=Decimal("0.00"), decimal_places=2, max_digits=6)
    primeiro_vencimento = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    # Regras de atraso
    tem_multa_atraso = forms.BooleanField(required=False, initial=True)
    multa_atraso_percent = forms.DecimalField(required=False, initial=Decimal("2.00"),
                                              min_value=Decimal("0.00"), decimal_places=2, max_digits=5)
    juros_mora_mensal_percent = forms.DecimalField(required=False, initial=Decimal("1.00"),
                                                   min_value=Decimal("0.00"), decimal_places=2, max_digits=5)

    def clean(self):
        cleaned = super().clean()
        tem_multa = cleaned.get("tem_multa_atraso", False)

        multa = cleaned.get("multa_atraso_percent")
        if not tem_multa:
            cleaned["multa_atraso_percent"] = Decimal("0.00")
        else:
            # se marcou "tem multa" e não preencheu, aplica default 2.00
            if multa is None:
                cleaned["multa_atraso_percent"] = Decimal("2.00")

        # juros mora: se não preencher, default 1.00
        if cleaned.get("juros_mora_mensal_percent") is None:
            cleaned["juros_mora_mensal_percent"] = Decimal("1.00")

        return cleaned