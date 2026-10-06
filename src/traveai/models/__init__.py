"""ORM models. Importing this package registers every table on `Base.metadata`."""

from traveai.models.base import Base
from traveai.models.delivery import Delivery
from traveai.models.drop_zone import DropZone
from traveai.models.event import Event
from traveai.models.merchant import ApiKey, Merchant
from traveai.models.mission import Mission
from traveai.models.quote import Quote
from traveai.models.vehicle import Vehicle

__all__ = [
    "ApiKey",
    "Base",
    "Delivery",
    "DropZone",
    "Event",
    "Merchant",
    "Mission",
    "Quote",
    "Vehicle",
]
