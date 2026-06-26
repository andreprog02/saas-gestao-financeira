from django import forms
from decimal import Decimal, InvalidOperation
from .models import Cliente


def _parse_brl(valor):
    if not valor or not valor.strip():
        return None
    limpo = valor.replace("R$", "").replace(" ", "").strip()
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    return Decimal(limpo)


class ClienteForm(forms.ModelForm):
    data_nascimento = forms.DateField(
        required=False,
        input_formats=["%d/%m/%Y"],
        widget=forms.TextInput(attrs={
            "class": "form-control", "id": "id_data_nascimento",
            "placeholder": "dd/mm/aaaa", "maxlength": "10",
            "inputmode": "numeric", "autocomplete": "off",
        })
    )

    renda_mensal = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control brl-mask", "placeholder": "0,00",
            "id": "id_renda_mensal", "inputmode": "decimal",
        })
    )

    outros_rendimentos = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control brl-mask", "placeholder": "0,00",
            "id": "id_outros_rendimentos", "inputmode": "decimal",
        })
    )

    estado_civil = forms.ChoiceField(
        required=False,
        choices=[("", "Selecione...")] + Cliente.ESTADO_CIVIL_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Cliente
        fields = [
            "nome_completo", "telefone", "cpf", "doc", "data_nascimento",
            "email", "profissao", "renda_mensal", "outros_rendimentos", "estado_civil",
            "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
            "parceiro_padrao", "percentual_comissao_padrao",
        ]
        widgets = {
            "nome_completo": forms.TextInput(attrs={"class": "form-control"}),
            "telefone": forms.TextInput(attrs={"class": "form-control", "id": "id_telefone"}),
            "cpf": forms.TextInput(attrs={"class": "form-control", "id": "id_cpf"}),
            "doc": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "email@exemplo.com"}),
            "profissao": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: Comerciante"}),
            "cep": forms.TextInput(attrs={"class": "form-control", "id": "id_cep"}),
            "logradouro": forms.TextInput(attrs={"class": "form-control", "id": "id_logradouro"}),
            "numero": forms.TextInput(attrs={"class": "form-control"}),
            "complemento": forms.TextInput(attrs={"class": "form-control"}),
            "bairro": forms.TextInput(attrs={"class": "form-control", "id": "id_bairro"}),
            "cidade": forms.TextInput(attrs={"class": "form-control", "id": "id_cidade"}),
            "uf": forms.TextInput(attrs={"class": "form-control", "id": "id_uf", "maxlength": "2"}),
            "parceiro_padrao": forms.Select(attrs={"class": "form-select"}),
            "percentual_comissao_padrao": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Todos os campos opcionais
        for field_name in self.fields:
            if field_name != "nome_completo" and field_name != "cpf":
                self.fields[field_name].required = False

        # Formata valores existentes pra exibição
        if self.instance and self.instance.pk:
            if self.instance.renda_mensal:
                self.initial["renda_mensal"] = f"{self.instance.renda_mensal:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if self.instance.outros_rendimentos:
                self.initial["outros_rendimentos"] = f"{self.instance.outros_rendimentos:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if self.instance.data_nascimento:
                self.initial["data_nascimento"] = self.instance.data_nascimento.strftime("%d/%m/%Y")

    def clean_renda_mensal(self):
        valor = self.cleaned_data.get("renda_mensal", "")
        try:
            return _parse_brl(valor)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError("Valor inválido.")

    def clean_outros_rendimentos(self):
        valor = self.cleaned_data.get("outros_rendimentos", "")
        try:
            return _parse_brl(valor)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError("Valor inválido.")