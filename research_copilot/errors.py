class CopilotError(Exception):
    def __init__(self, code: str, message: str, stage: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.stage, self.retryable = code, message, stage, retryable

    def as_dict(self):
        return {"code": self.code, "message": self.message,
                "stage": self.stage, "retryable": self.retryable}


class NumericEvidenceError(CopilotError):
    """A typed deterministic failure; the external QA error contract is unchanged."""
    def __init__(self):
        super().__init__("invalid_evidence_output", "陈述中的数字没有出现在所引原句中，未发布答案。", "evidence_validation", True)
