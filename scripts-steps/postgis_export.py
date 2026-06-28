"""PostGIS geometry export for IFC mesh data.

Exports the tessellated 3D geometry of IFC elements to a PostGIS table,
linked to Neo4j nodes by their GlobalId.

Table: ifc_geometry
  global_id    TEXT PRIMARY KEY   ← joins to Neo4j node.globalId
  ifc_type     TEXT
  name         TEXT
  floor_level  DOUBLE PRECISION
  volume       DOUBLE PRECISION
  surface_area DOUBLE PRECISION
  centroid_x/y/z  DOUBLE PRECISION
  geom         GEOMETRY(MULTIPOLYGONZ, 0)   ← tessellated mesh as triangles

Usage:
  from postgis_export import connect, setup_table, export_geometries

  conn = connect("host=localhost dbname=ifc user=postgres password=secret")
  setup_table(conn)
  export_geometries(conn, nodes)
  conn.close()

Spatial queries (examples):
  -- Elements within 5 m of a point:
  SELECT global_id, ifc_type, name
  FROM   ifc_geometry
  WHERE  ST_3DDWithin(geom, ST_MakePoint(10, 20, 3)::geometry, 5);

  -- Elements that overlap in 3D:
  SELECT a.global_id, b.global_id
  FROM   ifc_geometry a, ifc_geometry b
  WHERE  a.global_id <> b.global_id
  AND    ST_3DIntersects(a.geom, b.geom);
"""

import psycopg2
from psycopg2.extras import execute_batch

# ── Local default (edit or pass via CLI) ─────────────────────────────────────
PG_DSN = "host=localhost port=5432 dbname=postgis_ifc user=postgres password=postgres"
BATCH_SIZE = 100
# ─────────────────────────────────────────────────────────────────────────────


def connect(dsn: str = PG_DSN):
    """Open and return a psycopg2 connection to PostGIS."""
    return psycopg2.connect(dsn)


def setup_table(conn):
    """Create the PostGIS extension and ifc_geometry table if they don't exist.

    Safe to call on every run — uses IF NOT EXISTS everywhere.
    """
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ifc_geometry (
                global_id    TEXT             PRIMARY KEY,
                ifc_type     TEXT,
                name         TEXT,
                floor_level  DOUBLE PRECISION,
                volume       DOUBLE PRECISION,
                surface_area DOUBLE PRECISION,
                centroid_x   DOUBLE PRECISION,
                centroid_y   DOUBLE PRECISION,
                centroid_z   DOUBLE PRECISION,
                geom         GEOMETRY(MULTIPOLYGONZ, 0)
            );
        """)
        # Spatial index so 3D range / intersection queries are fast
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ifc_geometry_geom
            ON ifc_geometry USING GIST (geom);
        """)
        # Index on ifc_type for type-filtered queries
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ifc_geometry_type
            ON ifc_geometry (ifc_type);
        """)
    conn.commit()
    print("  PostGIS table ready   : ifc_geometry")


def _mesh_to_wkt(vertices: list, faces: list) -> str:
    """Convert flat vertices/faces arrays to WKT MULTIPOLYGON Z.

    Each triangle becomes one closed polygon ring:
      POLYGON Z ((x1 y1 z1, x2 y2 z2, x3 y3 z3, x1 y1 z1))

    All triangles are collected into a single MULTIPOLYGON Z so PostGIS
    stores the entire mesh as one geometry value per element.
    """
    polygons = []
    for i in range(0, len(faces), 3):
        i0 = faces[i]     * 3
        i1 = faces[i + 1] * 3
        i2 = faces[i + 2] * 3
        p0 = f"{vertices[i0]} {vertices[i0+1]} {vertices[i0+2]}"
        p1 = f"{vertices[i1]} {vertices[i1+1]} {vertices[i1+2]}"
        p2 = f"{vertices[i2]} {vertices[i2+1]} {vertices[i2+2]}"
        polygons.append(f"(({p0},{p1},{p2},{p0}))")
    return "MULTIPOLYGON Z (" + ",".join(polygons) + ")"


def export_geometries(conn, nodes: dict, batch_size: int = BATCH_SIZE):
    """Upsert all geometry-bearing nodes into the ifc_geometry table.

    Parameters
    ----------
    conn       : psycopg2 connection (from connect())
    nodes      : nodes dict returned by extract() in 02-all_relations.py
                 Each value must have a 'geometry' key (dict or None).
    batch_size : rows per database round-trip (tune for performance)

    Only nodes whose 'geometry' value is not None are exported.
    Uses INSERT … ON CONFLICT DO UPDATE so re-runs are idempotent.
    """
    rows = []
    for gid, node in nodes.items():
        geom = node.get('geometry')
        if not geom:
            continue

        verts = geom.get('vertices', [])
        faces = geom.get('faces', [])
        if not verts or not faces:
            continue

        c   = geom.get('centroid', [None, None, None])
        wkt = _mesh_to_wkt(verts, faces)

        rows.append((
            gid,
            node.get('ifcType'),
            node.get('name'),
            geom.get('floor_level'),
            geom.get('volume'),
            geom.get('surface_area'),
            c[0], c[1], c[2],
            wkt,
        ))

    if not rows:
        print("  No geometry to export to PostGIS.")
        return

    sql = """
        INSERT INTO ifc_geometry
            (global_id, ifc_type, name,
             floor_level, volume, surface_area,
             centroid_x, centroid_y, centroid_z,
             geom)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s,
             ST_GeomFromText(%s, 0))
        ON CONFLICT (global_id) DO UPDATE SET
            ifc_type     = EXCLUDED.ifc_type,
            name         = EXCLUDED.name,
            floor_level  = EXCLUDED.floor_level,
            volume       = EXCLUDED.volume,
            surface_area = EXCLUDED.surface_area,
            centroid_x   = EXCLUDED.centroid_x,
            centroid_y   = EXCLUDED.centroid_y,
            centroid_z   = EXCLUDED.centroid_z,
            geom         = EXCLUDED.geom;
    """

    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=batch_size)
    conn.commit()
    print(f"  Exported to PostGIS   : {len(rows)} geometries")
