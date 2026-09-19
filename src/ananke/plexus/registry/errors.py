"""Registry error hierarchy. Every error carries a stable machine-readable ``code``."""

from __future__ import annotations


class RegistryError(Exception):
    code = "REGISTRY_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code

    def __str__(self) -> str:
        return f"[{self.code}] {super().__str__()}"


class InvalidIdentityError(RegistryError):
    code = "INVALID_IDENTITY"


class InvalidVersionError(RegistryError):
    code = "INVALID_VERSION"


class InvalidRequirementError(RegistryError):
    code = "INVALID_REQUIREMENT"


class ManifestError(RegistryError):
    code = "INVALID_MANIFEST"


class PayloadError(RegistryError):
    code = "INVALID_PAYLOAD"


class VersionContentConflictError(RegistryError):
    code = "VERSION_CONTENT_CONFLICT"


class PolicyViolationError(RegistryError):
    code = "POLICY_VIOLATION"


class SecretDetectedError(RegistryError):
    code = "SECRET_DETECTED"


class NotFoundError(RegistryError):
    code = "NOT_FOUND"


class ResolutionError(RegistryError):
    code = "RESOLUTION_FAILED"


class IntegrityError(RegistryError):
    code = "INTEGRITY_ERROR"


class NotInitializedError(RegistryError):
    code = "REGISTRY_NOT_INITIALIZED"


class ImporterError(RegistryError):
    code = "IMPORTER_ERROR"


class DynamicIntrospectionError(RegistryError):
    code = "DYNAMIC_INTROSPECTION_FAILED"


class TranslationError(RegistryError):
    code = "RUNTIME_TRANSLATION_FAILED"


class QuarantinedError(RegistryError):
    code = "ARTIFACT_QUARANTINED"


class SchemaVersionError(RegistryError):
    code = "UNSUPPORTED_SCHEMA_VERSION"


class RemoteSourceError(RegistryError):
    code = "REMOTE_SOURCE_UNSUPPORTED"
