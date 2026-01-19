"""S2 Cell utilities for nearby_cell scouting."""
from __future__ import annotations

import math
from typing import List, Tuple
from LazyIVQueue.utils.logger import logger
import s2sphere


def get_s2_cell_id(lat: float, lon: float, level: int = 15) -> str:
    """
    Get S2 cell ID for a coordinate at specified level.

    Args:
        lat: Latitude
        lon: Longitude
        level: S2 cell level (default: 15, ~300m cells)

    Returns:
        S2 cell ID as string (hex representation)
    """
    logger.debug(f"Calculating S2 Cell ID for lat={lat}, lon={lon} at level={level}")
    try:
        latlng = s2sphere.LatLng.from_degrees(lat, lon)
        cell_id = s2sphere.CellId.from_lat_lng(latlng).parent(level)
        token = cell_id.to_token()
        logger.debug(f"S2 Calculation Result: {token} (ID: {cell_id.id()})")
        return token
    except Exception as e:
        logger.error(f"Failed to calculate S2 Cell ID: {e}")


def generate_honeycomb_coords(
    center_lat: float, center_lon: float, spacing_m: float = 70.0
) -> List[Tuple[float, float]]:
    """
    Generate honeycomb pattern coordinates for S2 cell scouting.

    Pattern (7 points):
          [top1]    [top2]
       [mid1] [CENTER] [mid2]
          [bot1]    [bot2]

    Args:
        center_lat: Center latitude
        center_lon: Center longitude
        spacing_m: Distance between points in meters (default: 70m)

    Returns:
        List of 7 (lat, lon) tuples in scout order
    """
    logger.debug(f"Generating honeycomb for center ({center_lat}, {center_lon}) with spacing {spacing_m}m")
    # Earth radius in meters
    EARTH_RADIUS_M = 6371000.0

    def offset_coords(lat: float, lon: float, dx_m: float, dy_m: float) -> Tuple[float, float]:
        """Offset coordinates by dx (east) and dy (north) meters."""
        # Convert to radians
        lat_rad = math.radians(lat)

        # Calculate new latitude (dy is north/south)
        new_lat = lat + (dy_m / EARTH_RADIUS_M) * (180 / math.pi)

        # Calculate new longitude (dx is east/west, adjusted for latitude)
        new_lon = lon + (dx_m / (EARTH_RADIUS_M * math.cos(lat_rad))) * (180 / math.pi)

        return (new_lat, new_lon)

    # Honeycomb offsets (dx, dy) in meters
    # Using hexagonal pattern with 70m spacing
    # Vertical spacing between rows: spacing * sin(60) = spacing * 0.866
    row_height = spacing_m * 0.866
    half_spacing = spacing_m / 2

    logger.debug(f"Honeycomb params: row_height={row_height:.2f}m, half_spacing={half_spacing:.2f}m")

    points_map = {
            "Center   ": (center_lat, center_lon),
            "Top Left ": offset_coords(center_lat, center_lon, -half_spacing, row_height),
            "Top Right": offset_coords(center_lat, center_lon, half_spacing, row_height),
            "Mid Left ": offset_coords(center_lat, center_lon, -spacing_m, 0),
            "Mid Right": offset_coords(center_lat, center_lon, spacing_m, 0),
            "Bot Left ": offset_coords(center_lat, center_lon, -half_spacing, -row_height),
            "Bot Right": offset_coords(center_lat, center_lon, half_spacing, -row_height),
        }

    # Log each point individually
    for name, (lat, lon) in points_map.items():
        logger.debug(f"Honeycomb Point [{name}]: {lat:.6f}, {lon:.6f}")

    coords = list(points_map.values())
    """
    coords = [
        # Center point (always first for scout)
        (center_lat, center_lon),
        # Top row (offset up by row_height, left and right by half_spacing)
        offset_coords(center_lat, center_lon, -half_spacing, row_height),   # top1
        offset_coords(center_lat, center_lon, half_spacing, row_height),    # top2
        # Middle row (left and right by full spacing)
        offset_coords(center_lat, center_lon, -spacing_m, 0),               # mid1
        offset_coords(center_lat, center_lon, spacing_m, 0),                # mid2
        # Bottom row (offset down by row_height, left and right by half_spacing)
        offset_coords(center_lat, center_lon, -half_spacing, -row_height),  # bot1
        offset_coords(center_lat, center_lon, half_spacing, -row_height),   # bot2
    ]
    """
    logger.info(f"Generated {len(coords)} scout points for S2 cell")
    return coords
