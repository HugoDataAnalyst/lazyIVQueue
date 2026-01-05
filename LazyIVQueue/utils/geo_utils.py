"""Geographic utility functions for distance calculations."""
from math import radians, sin, cos, sqrt, atan2

# Default threshold for coordinate matching (in meters)
COORDINATE_MATCH_THRESHOLD_METERS = 70.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two points in meters using Haversine formula.

    Args:
        lat1, lon1: First point coordinates (latitude, longitude)
        lat2, lon2: Second point coordinates (latitude, longitude)

    Returns:
        Distance in meters
    """
    R = 6371000  # Earth's radius in meters

    phi1, phi2 = radians(lat1), radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)

    a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def is_within_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    threshold_meters: float = COORDINATE_MATCH_THRESHOLD_METERS,
) -> bool:
    """
    Check if two points are within threshold distance.

    Args:
        lat1, lon1: First point coordinates
        lat2, lon2: Second point coordinates
        threshold_meters: Maximum distance in meters (default: 70m)

    Returns:
        True if distance between points is <= threshold
    """
    return haversine_distance(lat1, lon1, lat2, lon2) <= threshold_meters
