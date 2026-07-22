from __future__ import annotations

import math


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    if outside_china(lng, lat):
        return lng, lat
    axis = 6378245.0
    eccentricity = 0.006693421622965943
    delta_lat = gcj_transform_lat(lng - 105.0, lat - 35.0)
    delta_lng = gcj_transform_lng(lng - 105.0, lat - 35.0)
    radians = math.radians(lat)
    magic = 1 - eccentricity * math.sin(radians) ** 2
    root_magic = math.sqrt(magic)
    delta_lat = math.degrees(delta_lat / ((axis * (1 - eccentricity)) / (magic * root_magic)))
    delta_lng = math.degrees(delta_lng / ((axis / root_magic) * math.cos(radians)))
    return lng + delta_lng, lat + delta_lat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    if outside_china(lng, lat):
        return lng, lat
    original_lng, original_lat = lng, lat
    for _ in range(4):
        shifted_lng, shifted_lat = wgs84_to_gcj02(original_lng, original_lat)
        original_lng += lng - shifted_lng
        original_lat += lat - shifted_lat
    return original_lng, original_lat


def outside_china(lng: float, lat: float) -> bool:
    return lng < 72.004 or lng > 137.8347 or lat < 0.8293 or lat > 55.8271


def gcj_transform_lat(x: float, y: float) -> float:
    value = -100 + 2 * x + 3 * y + 0.2 * y ** 2 + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(y * math.pi) + 40 * math.sin(y / 3 * math.pi)) * 2 / 3
    value += (160 * math.sin(y / 12 * math.pi) + 320 * math.sin(y * math.pi / 30)) * 2 / 3
    return value


def gcj_transform_lng(x: float, y: float) -> float:
    value = 300 + x + 2 * y + 0.1 * x ** 2 + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(x * math.pi) + 40 * math.sin(x / 3 * math.pi)) * 2 / 3
    value += (150 * math.sin(x / 12 * math.pi) + 300 * math.sin(x / 30 * math.pi)) * 2 / 3
    return value
