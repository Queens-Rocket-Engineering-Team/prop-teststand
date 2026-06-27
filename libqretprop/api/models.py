from pydantic import BaseModel


class CommandResponse(BaseModel):
    status: str
    message: str

