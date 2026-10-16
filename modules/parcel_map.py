"""Validate and render optional parcel GeoJSON without affecting decisions."""

from __future__ import annotations

import json
import math
from html import escape
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import folium
import streamlit as st
from streamlit_folium import st_folium

GeometryStatus = Literal["valid", "missing", "invalid"]

STREET_TILES = "OpenStreetMap"
STREET_ATTRIBUTION = "&copy; OpenStreetMap contributors"
SATELLITE_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
SATELLITE_ATTRIBUTION = (
    "Tiles &copy; Esri — Sources: Esri, Maxar, Earthstar Geographics, "
    "and the GIS User Community"
)
SYNTHETIC_DEMO_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [
            [-73.1, 40.1],
            [-73.0, 40.1],
            [-73.0, 40.2],
            [-73.1, 40.2],
            [-73.1, 40.1],
        ]
    ],
}


@dataclass(frozen=True, slots=True)
class ParcelGeometryResult:
    status: GeometryStatus
    feature: dict[str, object] | None
    message: str
    bounds: tuple[tuple[float, float], tuple[float, float]] | None


def _coordinate_positions(coordinates: object) -> list[tuple[float, float]] | None:
    if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)):
        return None
    if (
        len(coordinates) >= 2
        and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in coordinates[:2]
        )
    ):
        try:
            longitude, latitude = (float(value) for value in coordinates[:2])
        except (OverflowError, TypeError, ValueError):
            return None
        if (
            not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            return None
        return [(longitude, latitude)]
    positions: list[tuple[float, float]] = []
    for child in coordinates:
        parsed = _coordinate_positions(child)
        if parsed is None:
            return None
        positions.extend(parsed)
    return positions or None


def _validate_polygon_rings(coordinates: object) -> bool:
    if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)):
        return False
    if not coordinates:
        return False
    for ring in coordinates:
        if not isinstance(ring, Sequence) or isinstance(ring, (str, bytes)):
            return False
        if len(ring) < 4:
            return False
        positions = _coordinate_positions(ring)
        if positions is None or positions[0] != positions[-1]:
            return False
    return True


def _validate_geometry(geometry: object) -> tuple[dict[str, object] | None, list[tuple[float, float]]]:
    if not isinstance(geometry, Mapping):
        return None, []
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        if not _validate_polygon_rings(coordinates):
            return None, []
    elif geometry_type == "MultiPolygon":
        if (
            not isinstance(coordinates, Sequence)
            or isinstance(coordinates, (str, bytes))
            or not coordinates
            or not all(_validate_polygon_rings(polygon) for polygon in coordinates)
        ):
            return None, []
    else:
        return None, []
    positions = _coordinate_positions(coordinates)
    if positions is None:
        return None, []
    return {"type": str(geometry_type), "coordinates": coordinates}, positions


def prepare_parcel_geojson(
    geojson: object,
    *,
    record_identifier: str,
    attributes: Mapping[str, object],
) -> ParcelGeometryResult:
    """Accept only valid Polygon/MultiPolygon geometry and replace all raw properties."""
    if geojson is None or geojson == "":
        return ParcelGeometryResult(
            "missing",
            None,
            "No parcel geometry is available for this record.",
            None,
        )
    if isinstance(geojson, str):
        try:
            geojson = json.loads(geojson)
        except json.JSONDecodeError:
            geojson = None
    if not isinstance(geojson, Mapping):
        return ParcelGeometryResult(
            "invalid",
            None,
            "Parcel geometry is not valid GeoJSON. No map was shown.",
            None,
        )

    source_features = (
        geojson.get("features")
        if geojson.get("type") == "FeatureCollection"
        else [geojson]
    )
    if (
        not isinstance(source_features, Sequence)
        or isinstance(source_features, (str, bytes))
        or not source_features
    ):
        return ParcelGeometryResult(
            "invalid",
            None,
            "Parcel GeoJSON contains no valid polygon features. No map was shown.",
            None,
        )

    safe_properties = {
        key: escape(value, quote=True) if isinstance(value, str) else value
        for key, value in attributes.items()
        if key in {"district", "village", "survey_or_plot_number", "land_area"}
        and isinstance(value, (str, int, float))
    }
    safe_features: list[dict[str, object]] = []
    positions: list[tuple[float, float]] = []
    for source_feature in source_features:
        if not isinstance(source_feature, Mapping):
            return ParcelGeometryResult(
                "invalid",
                None,
                "Parcel GeoJSON contains an invalid feature. No map was shown.",
                None,
            )
        geometry = (
            source_feature.get("geometry")
            if source_feature.get("type") == "Feature"
            else source_feature
        )
        safe_geometry, feature_positions = _validate_geometry(geometry)
        if safe_geometry is None:
            return ParcelGeometryResult(
                "invalid",
                None,
                "Parcel geometry must be a valid Polygon or MultiPolygon with "
                "well-formed WGS84 longitude/latitude coordinates. No map was shown.",
                None,
            )
        safe_features.append(
            {
                "type": "Feature",
                "geometry": safe_geometry,
                "properties": {
                    "record_identifier": escape(record_identifier, quote=True),
                    **safe_properties,
                },
            }
        )
        positions.extend(feature_positions)
    feature: dict[str, object] = {
        "type": "FeatureCollection",
        "features": safe_features,
    }
    longitudes = [longitude for longitude, _ in positions]
    latitudes = [latitude for _, latitude in positions]
    bounds = (
        (min(latitudes), min(longitudes)),
        (max(latitudes), max(longitudes)),
    )
    return ParcelGeometryResult(
        "valid",
        feature,
        "Parcel geometry passed structural coordinate checks.",
        bounds,
    )


def render_parcel_map(
    geojson: object,
    *,
    record_identifier: str,
    identifier_label: str = "Public record ID",
    attributes: Mapping[str, object],
    key: str,
    validation_status: str | None = None,
) -> ParcelGeometryResult:
    """Render a valid parcel polygon with selectable attributed basemaps."""
    result = prepare_parcel_geojson(
        geojson,
        record_identifier=record_identifier,
        attributes=attributes,
    )
    if result.status != "valid" or result.feature is None:
        if result.status == "missing":
            st.info(result.message)
            if st.button(
                "Preview a synthetic map example (not a real parcel)",
                key=f"{key}_synthetic_demo",
            ):
                st.warning(
                    "Synthetic demonstration only. This polygon and its location are "
                    "fabricated for UI illustration; they are not connected to this record "
                    "and are not official boundaries."
                )
                render_parcel_map(
                    SYNTHETIC_DEMO_GEOJSON,
                    record_identifier="SYNTHETIC DEMO — NOT A RECORD",
                    identifier_label="Synthetic demo",
                    attributes={
                        "district": "Synthetic example",
                        "village": "Synthetic example",
                        "survey_or_plot_number": "Synthetic example",
                        "land_area": "Synthetic example",
                    },
                    key=f"{key}_synthetic",
                    validation_status="unknown",
                )
        else:
            st.warning(result.message)
        return result

    status_colors = {
        "valid": "#2e8b57",
        "verified": "#2e8b57",
        "warning": "#e69500",
        "needs_review": "#e69500",
        "invalid": "#c0392b",
        "unknown": "#2878b5",
    }
    style_status = (validation_status or "unknown").casefold()
    color = status_colors.get(style_status, status_colors["unknown"])

    basemap_choice = st.selectbox(
        "Map basemap",
        ("Street map (OpenStreetMap)", "Satellite imagery (Esri)"),
        key=f"{key}_basemap",
    )
    map_view = folium.Map(
        location=[
            (result.bounds[0][0] + result.bounds[1][0]) / 2,
            (result.bounds[0][1] + result.bounds[1][1]) / 2,
        ],
        zoom_start=16,
        tiles=None,
        control_scale=True,
    )
    folium.TileLayer(
        tiles=STREET_TILES,
        attr=STREET_ATTRIBUTION,
        name="Street map (OpenStreetMap)",
        overlay=False,
        control=True,
        show=basemap_choice.startswith("Street"),
    ).add_to(map_view)
    folium.TileLayer(
        tiles=SATELLITE_TILES,
        attr=SATELLITE_ATTRIBUTION,
        name="Satellite imagery (Esri)",
        overlay=False,
        control=True,
        show=basemap_choice.startswith("Satellite"),
    ).add_to(map_view)
    feature_properties = result.feature["features"][0]["properties"]
    tooltip_fields = ["record_identifier"]
    tooltip_aliases = [identifier_label]
    for field_name, label in (
        ("district", "District"),
        ("village", "Village"),
        ("survey_or_plot_number", "Survey or plot number"),
        ("land_area", "Land area"),
    ):
        if field_name in feature_properties:
            tooltip_fields.append(field_name)
            tooltip_aliases.append(label)
    folium.GeoJson(
        result.feature,
        name="Parcel geometry",
        style_function=lambda _: {
            "color": color,
            "weight": 3,
            "fillColor": color,
            "fillOpacity": 0.3,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
        ),
    ).add_to(map_view)
    folium.LayerControl(collapsed=False).add_to(map_view)
    map_view.fit_bounds(result.bounds)
    st_folium(map_view, height=420, use_container_width=True, key=f"{key}_map")
    st.caption(
        "Map display is separate from application review and approval. Satellite "
        "imagery does not independently establish ownership or cadastral boundaries; "
        "displayed geometry is not represented as an official boundary."
    )
    return result
