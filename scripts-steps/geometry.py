"""Geometry extraction and analysis module for IFC entities.

Public API:
  make_settings()           → ifcopenshell.geom.settings
  extract_geometry(e, s)    → dict with derived properties + raw mesh, or None

Properties stored on Neo4j nodes (compact, query-friendly):
  centroid      list[float]  [cx, cy, cz]
  bounding_box  list[float]  [xmin, ymin, zmin, xmax, ymax, zmax]
  floor_level   float        min Z  (bottom of element in IFC length units)
  volume        float        approximate volume  (units³, closed meshes)
  surface_area  float        total surface area  (units²)

Properties used for PostGIS export only (not stored in Neo4j):
  vertices      list[float]  flat XYZ coordinate array
  faces         list[int]    flat triangle index array (3 indices per triangle)
"""

import math
import ifcopenshell
import ifcopenshell.geom


# ── Pure-Python vector helpers (no numpy dependency) ─────────────────────────

def _v(verts, i):
    """Return vertex i as (x, y, z) from a flat coordinate array."""
    return verts[i * 3], verts[i * 3 + 1], verts[i * 3 + 2]

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1],
            a[2]*b[0] - a[0]*b[2],
            a[0]*b[1] - a[1]*b[0])

def _norm(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)


# ── Derived property computation ──────────────────────────────────────────────

def _bounding_box(verts: list) -> list:
    """Return [xmin, ymin, zmin, xmax, ymax, zmax]."""
    xs = verts[0::3]; ys = verts[1::3]; zs = verts[2::3]
    return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]


def _centroid(verts: list) -> list:
    """Return the average vertex position [cx, cy, cz]."""
    n = len(verts) // 3
    return [
        sum(verts[0::3]) / n,
        sum(verts[1::3]) / n,
        sum(verts[2::3]) / n,
    ]


def _surface_area(verts: list, faces: list) -> float:
    """Sum of triangle areas over the entire mesh."""
    total = 0.0
    for i in range(0, len(faces), 3):
        v1 = _v(verts, faces[i])
        v2 = _v(verts, faces[i + 1])
        v3 = _v(verts, faces[i + 2])
        total += _norm(_cross(_sub(v2, v1), _sub(v3, v1))) / 2.0
    return total


def _volume(verts: list, faces: list) -> float:
    """Approximate signed volume via divergence theorem.

    Accurate for watertight (closed) meshes; an approximation for open ones.
    """
    total = 0.0
    for i in range(0, len(faces), 3):
        v1 = _v(verts, faces[i])
        v2 = _v(verts, faces[i + 1])
        v3 = _v(verts, faces[i + 2])
        # Scalar triple product: v1 · (v2 × v3)
        c = _cross(v2, v3)
        total += v1[0]*c[0] + v1[1]*c[1] + v1[2]*c[2]
    return abs(total) / 6.0


# ── Public API ────────────────────────────────────────────────────────────────

def make_settings() -> ifcopenshell.geom.settings:
    """Create and return ifcopenshell geometry settings.

    KEY: USE_WORLD_COORDS must be True.
    Without it, each element is tessellated in its own local frame (origin
    at 0,0,0), so all elements appear piled on each other in any 3D viewer.
    With it, the global placement transformation is applied and elements appear
    at their correct world positions.
    """
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    return settings


def extract_geometry(entity, settings) -> dict | None:
    """Tessellate an IFC entity and return derived properties + raw mesh.

    Parameters
    ----------
    entity   : ifcopenshell entity (e.g. IfcWall, IfcSlab)
    settings : settings object from make_settings()

    Returns
    -------
    dict  {
            # ─ Neo4j properties ─────────────────────────────────
            'centroid':     [cx, cy, cz],
            'bounding_box': [xmin, ymin, zmin, xmax, ymax, zmax],
            'floor_level':  float,        # zmin
            'surface_area': float,        # units²
            'volume':       float,        # units³

            # ─ PostGIS export (raw mesh) ─────────────────────────
            'vertices':     [x, y, z, ...],
            'faces':        [i0, i1, i2, ...],
          }
    None  if the entity has no 3D geometry.
    """
    try:
        shape  = ifcopenshell.geom.create_shape(settings, entity)
        verts  = list(shape.geometry.verts)
        faces  = list(shape.geometry.faces)

        if not verts or not faces:
            return None

        bbox = _bounding_box(verts)

        return {
            # Neo4j (compact derived properties)
            'centroid':     _centroid(verts),
            'bounding_box': bbox,
            'floor_level':  round(bbox[2], 6),
            'surface_area': round(_surface_area(verts, faces), 6),
            'volume':       round(_volume(verts, faces), 6),
            # PostGIS (raw tessellation)
            'vertices':     verts,
            'faces':        faces,
        }
    except Exception:
        # Non-geometric entities (IfcPropertySet, IfcProject, …) raise here
        return None
