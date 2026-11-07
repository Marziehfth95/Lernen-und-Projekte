"""Safe, explicit data-pipeline exceptions."""


class DataPipelineError(RuntimeError):
    """Base exception for expected ingestion failures."""


class InputFileMissingError(DataPipelineError):
    """Raised when the configured input is absent."""


class GzipIntegrityError(DataPipelineError):
    """Raised when the configured gzip stream cannot be read completely."""


class XESParsingError(DataPipelineError):
    """Raised when PM4Py cannot parse the XES event log."""


class ProcessedDataError(DataPipelineError):
    """Raised when required processed artifacts cannot be read."""

