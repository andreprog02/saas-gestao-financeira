from django.contrib.auth.models import AbstractUser
from django.db import models


class Empresa(models.Model):
    """Cada empresa/financeira é um tenant isolado no sistema."""
    razao_social = models.CharField("Razão Social", max_length=200)
    nome_fantasia = models.CharField("Nome Fantasia", max_length=200, blank=True, default="")
    cnpj = models.CharField("CNPJ", max_length=18, unique=True)
    telefone = models.CharField("Telefone", max_length=20, blank=True, default="")
    email = models.EmailField("E-mail", blank=True, default="")
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["razao_social"]
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    def __str__(self):
        return self.nome_fantasia or self.razao_social


class Usuario(AbstractUser):
    """Usuário customizado com vínculo a empresa e nível de permissão."""

    class Cargo(models.TextChoices):
        OPERADOR = "OPERADOR", "Operador"
        ANALISTA = "ANALISTA", "Analista"
        GERENTE = "GERENTE", "Gerente"
        ADMIN = "ADMIN", "Administrador"

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios",
        verbose_name="Empresa",
    )
    cargo = models.CharField(
        "Cargo / Nível",
        max_length=20,
        choices=Cargo.choices,
        default=Cargo.OPERADOR,
    )
    telefone = models.CharField("Telefone", max_length=20, blank=True, default="")
    foto = models.ImageField("Foto", upload_to="usuarios/fotos/", blank=True, null=True)

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        ordering = ["first_name", "last_name"]

    def __str__(self):
        nome = self.get_full_name() or self.username
        return f"{nome} ({self.get_cargo_display()})"

    # ---------- helpers de permissão ----------

    @property
    def is_operador(self):
        return self.cargo == self.Cargo.OPERADOR

    @property
    def is_analista(self):
        return self.cargo in (self.Cargo.ANALISTA, self.Cargo.GERENTE, self.Cargo.ADMIN)

    @property
    def is_gerente(self):
        return self.cargo in (self.Cargo.GERENTE, self.Cargo.ADMIN)

    @property
    def is_admin_empresa(self):
        return self.cargo == self.Cargo.ADMIN

    def tem_alcada(self, valor):
        """
        Verifica se o usuário pode aprovar até determinado valor.
        Limites configuráveis — ajuste conforme sua política.
        """
        from decimal import Decimal
        limites = {
            self.Cargo.OPERADOR: Decimal("0"),         # Não aprova
            self.Cargo.ANALISTA: Decimal("10000.00"),   # Até 10k
            self.Cargo.GERENTE: Decimal("50000.00"),    # Até 50k
            self.Cargo.ADMIN: Decimal("999999999.99"),  # Sem limite
        }
        return Decimal(str(valor)) <= limites.get(self.cargo, Decimal("0"))
