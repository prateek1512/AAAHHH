from __future__ import annotations

import modules.parcel_map as parcel_map
from modules.parcel_map import prepare_parcel_geojson


SYNTHETIC_POLYGON = {
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


def test_missing_geometry_returns_a_helpful_unavailable_result() -> None:
    result = prepare_parcel_geojson(
        None,
        record_identifier="LR-SYNTHETIC-1",
        attributes={"district": "Synthetic District"},
    )

    assert result.status == "missing"
    assert result.feature is None
    assert "No parcel geometry" in result.message


def test_invalid_geometry_is_not_renderable() -> None:
    result = prepare_parcel_geojson(
        {"type": "Polygon", "coordinates": [[[200, 40], [201, 40], [200, 40]]]},
        record_identifier="LR-SYNTHETIC-2",
        attributes={},
    )

    assert result.status == "invalid"
    assert result.feature is None
    assert "No map was shown" in result.message


def test_valid_synthetic_polygon_is_display_safe_and_excludes_raw_properties() -> None:
    result = prepare_parcel_geojson(
        {
            "type": "Feature",
            "geometry": SYNTHETIC_POLYGON,
            "properties": {
                "personal_identifier": "PRIVATE-SYNTHETIC",
                "officer_note": "internal",
            },
        },
        record_identifier="LR-SYNTHETIC-3",
        attributes={
            "district": "Synthetic District",
            "village": "Example Village",
            "survey_or_plot_number": "PLOT-SYNTHETIC-3",
            "land_area": "1 synthetic unit",
            "personal_identifier": "PRIVATE-SYNTHETIC",
        },
    )

    assert result.status == "valid"
    assert result.feature is not None
    assert result.feature["type"] == "FeatureCollection"
    feature = result.feature["features"][0]
    assert feature["geometry"] == SYNTHETIC_POLYGON
    assert feature["properties"] == {
        "record_identifier": "LR-SYNTHETIC-3",
        "district": "Synthetic District",
        "village": "Example Village",
        "survey_or_plot_number": "PLOT-SYNTHETIC-3",
        "land_area": "1 synthetic unit",
    }
    assert result.bounds == ((40.1, -73.1), (40.2, -73.0))


def test_unclosed_rings_and_non_polygon_geometry_are_rejected() -> None:
    open_ring = {
        "type": "Polygon",
        "coordinates": [[[1, 1], [2, 1], [2, 2], [1, 2]]],
    }
    line = {"type": "LineString", "coordinates": [[1, 1], [2, 2]]}

    for geometry in (open_ring, line):
        result = prepare_parcel_geojson(
            geometry,
            record_identifier="LR-SYNTHETIC-4",
            attributes={},
        )
        assert result.status == "invalid"
        assert result.feature is None


def test_valid_synthetic_feature_collection_is_supported() -> None:
    result = prepare_parcel_geojson(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": SYNTHETIC_POLYGON, "properties": {}},
                {"type": "Feature", "geometry": SYNTHETIC_POLYGON, "properties": {}},
            ],
        },
        record_identifier="LR-SYNTHETIC-5",
        attributes={"village": "Example Village"},
    )

    assert result.status == "valid"
    assert result.feature is not None
    assert len(result.feature["features"]) == 2


def test_rendered_map_offers_attributed_street_and_satellite_layers(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        parcel_map.st,
        "selectbox",
        lambda *args, **kwargs: "Satellite imagery (Esri)",
    )
    monkeypatch.setattr(
        parcel_map,
        "st_folium",
        lambda map_object, **kwargs: captured.update(map=map_object),
    )
    result = parcel_map.render_parcel_map(
        SYNTHETIC_POLYGON,
        record_identifier="LR-SYNTHETIC-6",
        attributes={"district": "Synthetic District"},
        key="synthetic-parcel",
        validation_status="warning",
    )

    assert result.status == "valid"
    map_object = captured["map"]
    tile_layers = [
        item
        for item in map_object._children.values()
        if isinstance(item, parcel_map.folium.TileLayer)
    ]
    assert {layer.layer_name for layer in tile_layers} == {
        "Street map (OpenStreetMap)",
        "Satellite imagery (Esri)",
    }
    assert all(layer.options.get("attribution") for layer in tile_layers)
    geojson_layer = next(
        item
        for item in map_object._children.values()
        if isinstance(item, parcel_map.folium.GeoJson)
    )
    assert geojson_layer.style_function(geojson_layer.data["features"][0])["color"] == (
        "#e69500"
    )


def test_missing_geometry_can_show_only_an_explicitly_labeled_synthetic_demo(
    monkeypatch,
) -> None:
    captured = {}
    warnings = []
    monkeypatch.setattr(parcel_map.st, "info", lambda message: None)
    monkeypatch.setattr(parcel_map.st, "button", lambda *args, **kwargs: True)
    monkeypatch.setattr(parcel_map.st, "warning", warnings.append)
    monkeypatch.setattr(
        parcel_map.st,
        "selectbox",
        lambda *args, **kwargs: "Street map (OpenStreetMap)",
    )
    monkeypatch.setattr(
        parcel_map,
        "st_folium",
        lambda map_object, **kwargs: captured.update(map=map_object),
    )

    result = parcel_map.render_parcel_map(
        None,
        record_identifier="LR-NO-GEOMETRY",
        attributes={},
        key="missing-geometry",
    )

    assert result.status == "missing"
    assert "Synthetic demonstration only" in warnings[0]
    feature_layer = next(
        item
        for item in captured["map"]._children.values()
        if isinstance(item, parcel_map.folium.GeoJson)
    )
    assert feature_layer.data["features"][0]["properties"]["record_identifier"] == (
        "SYNTHETIC DEMO — NOT A RECORD"
    )
