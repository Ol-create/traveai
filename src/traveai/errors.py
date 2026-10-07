from fastapi import HTTPException


class ApiError(HTTPException):
    """Error with a stable machine-readable code, returned as
    `{"detail": {"code": "quote_expired", "message": "..."}}`."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})
        self.code = code
