from sqlalchemy import Column, Integer, String
from geoalchemy2 import Geometry
from app.db.session import Base


class Facility(Base):
    """
    Represents a single point-of-interest seeded from OSM or the
    California food-desert dataset.

    geom is stored as EPSG:4326 (WGS84). The GIST index is created
    in init_db — not here — so it survives table re-creation without
    duplicate-index errors.
    """

    __tablename__ = "facilities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    facility_type = Column(String, nullable=False)  # hospital | grocery | ev_charger | school
    geom = Column(Geometry("POINT", srid=4326), nullable=False)
    osm_id = Column(String, nullable=True)
    address = Column(String, nullable=True)
