from pathlib import Path

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)

ROOT = Path("/Users/chun/Develop/flood/珊瑚河数据")


def add_layer(group, name, relative_path, style, visible=True):
    path = ROOT / relative_path
    layer = QgsVectorLayer(str(path), name, "ogr")
    if not layer.isValid():
        iface.messageBar().pushMessage("珊瑚河数据", f"无法加载: {path}", level=Qgis.Warning)
        return None

    if style["type"] == "fill":
        symbol = QgsFillSymbol.createSimple(
            {
                "color": style["color"],
                "outline_color": style.get("outline", "#333333"),
                "outline_width": style.get("outline_width", "0.15"),
            }
        )
    elif style["type"] == "line":
        symbol = QgsLineSymbol.createSimple(
            {
                "color": style["color"],
                "width": style.get("width", "0.5"),
                "line_style": style.get("line_style", "solid"),
            }
        )
    else:
        symbol = QgsMarkerSymbol.createSimple(
            {
                "color": style["color"],
                "outline_color": style.get("outline", "#ffffff"),
                "outline_width": style.get("outline_width", "0.4"),
                "size": style.get("size", "3"),
                "name": style.get("shape", "circle"),
            }
        )

    if "opacity" in style:
        symbol.setOpacity(style["opacity"])

    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    QgsProject.instance().addMapLayer(layer, False)
    node = group.addLayer(layer)
    node.setItemVisibilityChecked(visible)
    return layer


project = QgsProject.instance()
project.clear()
project.setCrs(QgsCoordinateReferenceSystem("EPSG:4546"))

root = project.layerTreeRoot()

base_group = root.addGroup("基础边界与路网")
flood_group = root.addGroup("淹没图层")
assets_group = root.addGroup("重要设施与水利工程")
evac_group = root.addGroup("避洪转移")

layers = []

layers.append(
    add_layer(
        base_group,
        "珊瑚河流域范围",
        "1.流域边界/珊瑚河流域范围.shp",
        {"type": "fill", "color": "255,255,255,0", "outline": "#222222", "outline_width": "0.8"},
    )
)
layers.append(
    add_layer(
        base_group,
        "珊瑚河县界",
        "2.县界/珊瑚河县界.shp",
        {"type": "fill", "color": "255,255,255,0", "outline": "#555555", "outline_width": "0.5"},
    )
)
layers.append(
    add_layer(
        base_group,
        "公路",
        "5.路网/公路-线.shp",
        {"type": "line", "color": "#777777", "width": "0.35"},
    )
)

flood_specs = [
    ("5年一遇淹没范围", "45050092hsfx0001.shp", "#9ecae1", 0.35, False),
    ("10年一遇淹没范围", "45050092hsfx0002.shp", "#6baed6", 0.32, False),
    ("20年一遇淹没范围", "45050092hsfx0003.shp", "#4292c6", 0.30, True),
    ("50年一遇淹没范围", "45050092hsfx0004.shp", "#2171b5", 0.28, False),
    ("100年一遇淹没范围", "45050092hsfx0005.shp", "#084594", 0.25, True),
]
for name, filename, color, opacity, visible in flood_specs:
    layers.append(
        add_layer(
            flood_group,
            name,
            f"6.淹没图层/45050092_珊瑚河/{filename}",
            {"type": "fill", "color": color, "outline": color, "outline_width": "0.05", "opacity": opacity},
            visible=visible,
        )
    )

layers.append(
    add_layer(
        assets_group,
        "学校",
        "4.重要设施/学校.shp",
        {"type": "marker", "color": "#f59e0b", "outline": "#ffffff", "size": "2.6", "shape": "triangle"},
    )
)
layers.append(
    add_layer(
        assets_group,
        "医院",
        "4.重要设施/医院.shp",
        {"type": "marker", "color": "#dc2626", "outline": "#ffffff", "size": "3.0", "shape": "cross"},
    )
)
layers.append(
    add_layer(
        assets_group,
        "政府",
        "4.重要设施/政府.shp",
        {"type": "marker", "color": "#7c3aed", "outline": "#ffffff", "size": "2.8", "shape": "square"},
    )
)
layers.append(
    add_layer(
        assets_group,
        "桥梁",
        "3.水利工程/桥梁.shp",
        {"type": "marker", "color": "#111827", "outline": "#ffffff", "size": "2.0"},
        visible=False,
    )
)

layers.append(
    add_layer(
        evac_group,
        "转移路线",
        "8.避洪转移/转移路线.shp",
        {"type": "line", "color": "#ef4444", "width": "0.75"},
    )
)
layers.append(
    add_layer(
        evac_group,
        "安置点",
        "8.避洪转移/安置点.shp",
        {"type": "marker", "color": "#16a34a", "outline": "#ffffff", "size": "3.2", "shape": "diamond"},
    )
)
layers.append(
    add_layer(
        evac_group,
        "转移单元",
        "8.避洪转移/转移单元.shp",
        {"type": "marker", "color": "#f97316", "outline": "#ffffff", "size": "2.6"},
        visible=False,
    )
)

extent = QgsRectangle()
for layer in [layer for layer in layers if layer is not None]:
    if layer.name() in {"20年一遇淹没范围", "100年一遇淹没范围", "珊瑚河流域范围"}:
        if extent.isNull():
            extent = layer.extent()
        else:
            extent.combineExtentWith(layer.extent())

if not extent.isNull():
    iface.mapCanvas().setExtent(extent)
    iface.mapCanvas().refresh()

project.write("/Users/chun/Develop/flood/珊瑚河数据/珊瑚河_查看.qgz")

try:
    import qgis.utils

    if "qgis_mcp_plugin" not in qgis.utils.plugins:
        qgis.utils.loadPlugin("qgis_mcp_plugin")
        qgis.utils.startPlugin("qgis_mcp_plugin")
    plugin = qgis.utils.plugins.get("qgis_mcp_plugin")
    if plugin and not getattr(plugin, "server", None):
        plugin.port_spin.setValue(9876)
        plugin.action.setChecked(True)
        plugin.toggle_server(True)
except Exception as exc:
    iface.messageBar().pushMessage("QGIS MCP", f"插件服务未自动启动: {exc}", level=Qgis.Warning)

iface.messageBar().pushMessage("珊瑚河数据", "核心图层已加载，工程已保存为 珊瑚河_查看.qgz", level=Qgis.Success)
