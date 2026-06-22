from django import forms
from decimal import Decimal, InvalidOperation
from .models import Cliente


class ClienteForm(forms.ModelForm):
    data_nascimento = forms.DateField(
        required=False,
        input_formats=["%d/%m/%Y"],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "id": "id_data_nascimento",
            "placeholder": "dd/mm/aaaa",
            "maxlength": "10",
            "inputmode": "numeric",
            "autocomplete": "off",
        })
    )

    renda_mensal = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "0,00",
            "id": "id_renda_mensal",
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
            "profissao", "renda_mensal", "estado_civil",
            "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
        ]
        widgets = {
            "nome_completo": forms.TextInput(attrs={"class": "form-control"}),
            "telefone": forms.TextInput(attrs={"class": "form-control", "id": "id_telefone"}),
            "cpf": forms.TextInput(attrs={"class": "form-control", "id": "id_cpf"}),
            "doc": forms.TextInput(attrs={"class": "form-control"}),
            "profissao": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: Comerciante"}),
            "cep": forms.TextInput(attrs={"class": "form-control", "id": "id_cep"}),
            "logradouro": forms.TextInput(attrs={"class": "form-control", "id": "id_logradouro"}),
            "numero": forms.TextInput(attrs={"class": "form-control"}),
            "complemento": forms.TextInput(attrs={"class": "form-control"}),
            "bairro": forms.TextInput(attrs={"class": "form-control", "id": "id_bairro"}),
            "cidade": forms.TextInput(attrs={"class": "form-control", "id": "id_cidade"}),
            "uf": forms.TextInput(attrs={"class": "form-control", "id": "id_uf", "maxlength": "2"}),
        }

    def clean_renda_mensal(self):
        valor = self.cleaned_data.get("renda_mensal", "")
        if not valor or valor.strip() == "":
            return None
        try:
            limpo = valor.replace("R$", "").replace(" ", "").strip()
            if "," in limpo and "." in limpo:
                limpo = limpo.replace(".", "").replace(",", ".")
            elif "," in limpo:
                limpo = limpo.replace(",", ".")
            return Decimal(limpo)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError("Valor inválido para renda.")