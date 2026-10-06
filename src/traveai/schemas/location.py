from pydantic import BaseModel, ConfigDict, Field


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    address: str | None = Field(default=None, max_length=300)
