from django.db import models


class EmpresaManager(models.Manager):
    """
    Manager que filtra automaticamente por empresa.
    
    Uso nos models:
        class Cliente(models.Model):
            empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
            ...
            objects = EmpresaManager()
    
    Uso nas views:
        clientes = Cliente.objects.da_empresa(request.empresa)
    """

    def da_empresa(self, empresa):
        """Retorna queryset filtrado pela empresa."""
        if empresa is None:
            return self.none()
        return self.filter(empresa=empresa)


class EmpresaMixin(models.Model):
    """
    Mixin para adicionar campo empresa em qualquer model.
    
    Uso:
        class Cliente(EmpresaMixin, models.Model):
            nome = models.CharField(...)
            ...
    
    Isso adiciona automaticamente:
    - campo `empresa` (ForeignKey)
    - manager `objects` com método `da_empresa()`
    """
    empresa = models.ForeignKey(
        "usuarios.Empresa",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s",
        verbose_name="Empresa",
        null=True,   # temporariamente nullable para migração
        blank=True,
    )

    objects = EmpresaManager()

    class Meta:
        abstract = True
