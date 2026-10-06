import pytest
from pydantic import ValidationError

from traveai.schemas.location import Location
from traveai.schemas.payload import Payload

BASE = {"category": "medical", "weight_kg": 0.5, "length_cm": 20, "width_cm": 15, "height_cm": 10}


def test_valid_medical_prescription():
    p = Payload(**BASE, prescription=True, temperature_controlled=True)
    assert p.prescription and p.temperature_controlled


def test_prescription_requires_medical_category():
    with pytest.raises(ValidationError, match="prescription"):
        Payload(**{**BASE, "category": "food"}, prescription=True)


@pytest.mark.parametrize("weight", [0, -1, 25.1])
def test_weight_bounds(weight):
    with pytest.raises(ValidationError):
        Payload(**{**BASE, "weight_kg": weight})


def test_unknown_category_rejected():
    with pytest.raises(ValidationError):
        Payload(**{**BASE, "category": "weapons"})


@pytest.mark.parametrize(("lat", "lng"), [(91, 0), (0, 181), (-90.1, 0)])
def test_location_bounds(lat, lng):
    with pytest.raises(ValidationError):
        Location(lat=lat, lng=lng)
