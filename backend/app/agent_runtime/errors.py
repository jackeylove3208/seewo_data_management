class ExternalWriteRecoveryRequired(RuntimeError):
    """The external target may have committed and must be replayed safely."""
