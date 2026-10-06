from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from traveai.domain.enums import PayloadCategory

# Hard sanity ceiling: under FAA Part 107 the whole aircraft (drone + cargo) must be < 55 lb
# (~25 kg), so no payload can be heavier. Per-drone limits are checked by the rules engine.
MAX_PAYLOAD_KG = 25.0
MAX_DIMENSION_CM = 200.0


class Payload(BaseModel):
    """What is being delivered."""

    model_config = ConfigDict(frozen=True)

    category: PayloadCategory
    weight_kg: float = Field(gt=0, le=MAX_PAYLOAD_KG)
    length_cm: float = Field(gt=0, le=MAX_DIMENSION_CM)
    width_cm: float = Field(gt=0, le=MAX_DIMENSION_CM)
    height_cm: float = Field(gt=0, le=MAX_DIMENSION_CM)
    temperature_controlled: bool = Field(
        default=False, description="Needs a cooled/heated compartment (vaccines, hot food)."
    )
    fragile: bool = False
    prescription: bool = Field(
        default=False, description="Prescription medicine: requires recipient PIN on delivery."
    )
    description: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _prescription_must_be_medical(self) -> Self:
        if self.prescription and self.category != PayloadCategory.MEDICAL:
            raise ValueError("prescription payloads must have category 'medical'")
        return self
