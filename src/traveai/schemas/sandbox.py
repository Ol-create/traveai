from pydantic import BaseModel, Field

from traveai.workers import MAX_SPEED, MIN_SPEED


class SimulatorStatus(BaseModel):
    running: bool = Field(description="Whether a simulator worker is active (holds its lease).")
    speed: float = Field(description="Simulated seconds per real second.")
    failure_rate: float = Field(description="Chance each flight gets a random failure.")
    night_operations: bool
    controls_enabled: bool = Field(description="Whether PATCH /v1/test/simulator is allowed.")


class SimulatorUpdate(BaseModel):
    speed: float = Field(ge=MIN_SPEED, le=MAX_SPEED, examples=[10])
