"""Errors in the shape Asaas returns them: {"errors": [{"code": ..., "description": ...}]}."""

from __future__ import annotations


class AsaasError(Exception):
    def __init__(self, status_code: int, code: str, description: str) -> None:
        super().__init__(description)
        self.status_code = status_code
        self.code = code
        self.description = description

    def body(self) -> dict:
        return {"errors": [{"code": self.code, "description": self.description}]}


class NotFound(Exception):
    """Asaas answers unknown ids with a bare 404."""


def invalid(code: str, description: str) -> AsaasError:
    return AsaasError(400, code, description)


def unauthorized() -> AsaasError:
    return AsaasError(401, "invalid_access_token", "A chave de API fornecida é inválida")
