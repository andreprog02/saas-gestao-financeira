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
        OPERACIONAL = "OPERACIONAL", "Operacional"
        CAIXA = "CAIXA", "Caixa"
        SUPERVISOR = "SUPERVISOR", "Supervisor"
        GERENTE = "GERENTE", "Gerente"
        DIRETOR = "DIRETOR", "Diretor"

    empresa = models.ForeignKey(
        Empresa, on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="usuarios", verbose_name="Empresa",
    )
    cargo = models.CharField(
        "Cargo / Nível", max_length=20,
        choices=Cargo.choices, default=Cargo.OPERACIONAL,
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
    def nivel(self):
        """Retorna nível numérico do cargo."""
        niveis = {
            "OPERACIONAL": 1,
            "CAIXA": 1,
            "SUPERVISOR": 2,
            "GERENTE": 3,
            "DIRETOR": 4,
        }
        return niveis.get(self.cargo, 0)

    @property
    def is_supervisor_ou_acima(self):
        return self.nivel >= 2

    @property
    def is_gerente_ou_acima(self):
        return self.nivel >= 3

    @property
    def is_diretor(self):
        return self.cargo == self.Cargo.DIRETOR

    def tem_permissao(self, modulo, nivel_minimo="VISUALIZAR"):
        """Verifica se o usuário tem permissão para um módulo."""
        # Diretor tem acesso total
        if self.is_diretor or self.is_superuser:
            return True

        niveis = {"NENHUM": 0, "VISUALIZAR": 1, "OPERAR": 2, "GERENCIAR": 3}
        nivel_req = niveis.get(nivel_minimo, 0)

        perm = self.permissoes_modulo.filter(modulo=modulo).first()
        if not perm:
            return False

        nivel_user = niveis.get(perm.nivel, 0)
        return nivel_user >= nivel_req

    def tem_alcada(self, valor):
        """Verifica alçada de aprovação por valor."""
        from decimal import Decimal
        limites = {
            "OPERACIONAL": Decimal("0"),
            "CAIXA": Decimal("0"),
            "SUPERVISOR": Decimal("10000.00"),
            "GERENTE": Decimal("50000.00"),
            "DIRETOR": Decimal("999999999.99"),
        }
        return Decimal(str(valor)) <= limites.get(self.cargo, Decimal("0"))


class PermissaoModulo(models.Model):
    """Permissão de acesso por módulo do sistema — configurável por usuário."""

    MODULO_CHOICES = [
        ("CLIENTES", "Clientes"),
        ("ESTEIRA", "Esteira de Crédito"),
        ("CONTRATOS", "Gestão de Contratos"),
        ("FINANCEIRO", "Fluxo de Caixa"),
        ("CONCILIACAO", "Conciliação Bancária"),
        ("COBRANCA", "Cobrança"),
        ("CONTAS_PAGAR", "Contas a Pagar"),
        ("RECEBIVEIS", "Recebíveis"),
        ("USUARIOS", "Gestão de Usuários"),
        ("RELATORIOS", "Relatórios"),
    ]

    NIVEL_CHOICES = [
        ("NENHUM", "Sem Acesso"),
        ("VISUALIZAR", "Visualizar"),
        ("OPERAR", "Operar"),
        ("GERENCIAR", "Gerenciar"),
    ]

    usuario = models.ForeignKey(
        Usuario, on_delete=models.CASCADE, related_name="permissoes_modulo"
    )
    modulo = models.CharField("Módulo", max_length=20, choices=MODULO_CHOICES)
    nivel = models.CharField("Nível de Acesso", max_length=15, choices=NIVEL_CHOICES, default="NENHUM")

    class Meta:
        unique_together = [("usuario", "modulo")]
        ordering = ["modulo"]
        verbose_name = "Permissão de Módulo"
        verbose_name_plural = "Permissões de Módulos"

    def __str__(self):
        return f"{self.usuario.username} — {self.get_modulo_display()}: {self.get_nivel_display()}"
