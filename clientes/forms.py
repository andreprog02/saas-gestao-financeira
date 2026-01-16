from django import forms
from .models import Cliente


class ClienteForm(forms.ModelForm):
    # Aceitar dd/mm/aaaa e converter pro DateField
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

    class Meta:
        model = Cliente
        fields = [
            "nome_completo",
            "telefone",
            "cpf",
            "doc",
            "data_nascimento",   # ✅ NOVO
            "cep",
            "logradouro",
            "numero",
            "complemento",
            "bairro",
            "cidade",
            "uf",
        ]
        widgets = {
            "nome_completo": forms.TextInput(attrs={"class": "form-control"}),
            "telefone": forms.TextInput(attrs={"class": "form-control", "id": "id_telefone"}),
            "cpf": forms.TextInput(attrs={"class": "form-control", "id": "id_cpf"}),
            "doc": forms.TextInput(attrs={"class": "form-control"}),

            "cep": forms.TextInput(attrs={"class": "form-control", "id": "id_cep"}),
            "logradouro": forms.TextInput(attrs={"class": "form-control", "id": "id_logradouro"}),
            "numero": forms.TextInput(attrs={"class": "form-control"}),
            "complemento": forms.TextInput(attrs={"class": "form-control"}),
            "bairro": forms.TextInput(attrs={"class": "form-control", "id": "id_bairro"}),
            "cidade": forms.TextInput(attrs={"class": "form-control", "id": "id_cidade"}),
            "uf": forms.TextInput(attrs={"class": "form-control", "id": "id_uf", "maxlength": "2"}),
        }
