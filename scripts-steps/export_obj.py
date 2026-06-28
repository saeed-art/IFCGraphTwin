"""Export IFC geometry from PostGIS to Wavefront OBJ format.

Reads tessellated geometry from the ifc_geometry table using ST_DumpPoints,
which returns individual vertex coordinates as rows — no WKT string parsing.

Each row from the DB: (global_id, ifc_type, name, poly_idx, pt_idx, x, y, z)
  poly_idx  → which triangle this vertex belongs to
  pt_idx    → position within the triangle ring (1,2,3,4 — 4th closes the ring)

OBJ files open in:
  - Windows 3D Viewer  (built-in, double-click)
  - Blender            (File -> Import -> Wavefront .obj)
  - MeshLab            (free, lightweight)

Usage:
  python scripts-steps/export_obj.py
  python scripts-steps/export_obj.py --types IfcWall IfcSlab --out wall_slab.obj
  python scripts-steps/export_obj.py --types              # export everything in DB
"""

import argparse
import os
import sys

import psycopg2

# ── Defaults ──────────────────────────────────────────────────────────────────
PG_DSN   = "host=localhost port=5432 dbname=postgis_ifc user=postgres password=4262890"
OUT_FILE = "d:/Projects/01-IFC_structure/output/model.obj"

DEFAULT_TYPES = [
    'IfcWall', 'IfcWallStandardCase',
    'IfcSlab',
    'IfcDoor',
    'IfcWindow',
    'IfcColumn',
    'IfcBeam',
    'IfcRoof',
    'IfcStair',
    'IfcRailing',
    'IfcPlate',
    'IfcMember',
]
# ─────────────────────────────────────────────────────────────────────────────

TYPE_COLORS = {
    'IfcWall':              (0.80, 0.75, 0.65),
    'IfcWallStandardCase':  (0.80, 0.75, 0.65),
    'IfcSlab':              (0.55, 0.55, 0.55),
    'IfcDoor':              (0.55, 0.30, 0.10),
    'IfcWindow':            (0.60, 0.85, 0.95),
    'IfcColumn':            (0.70, 0.70, 0.70),
    'IfcBeam':              (0.65, 0.50, 0.35),
    'IfcRoof':              (0.70, 0.35, 0.25),
    'IfcStair':             (0.75, 0.70, 0.60),
    'IfcRailing':           (0.40, 0.40, 0.40),
    '_default':             (0.80, 0.80, 0.80),
}


# ── Database fetch ────────────────────────────────────────────────────────────

def fetch_geometry(conn, types: list | None) -> list[dict]:
    """Fetch tessellated geometry from PostGIS using ST_DumpPoints.

    ST_DumpPoints explodes each MULTIPOLYGON Z into individual vertex rows,
    with a path array [poly_idx, ring_idx, pt_idx] identifying each point.

    Each triangle = one polygon with 4 points (last closes the ring).
    We take pt_idx 1,2,3 (skip 4) to get the 3 unique triangle vertices.

    Returns a list of element dicts:
    [
        {
            'global_id': str,
            'ifc_type':  str,
            'name':      str,
            'verts':     [(x,y,z), ...],   # flat vertex list for this element
            'tris':      [(i0,i1,i2), ...] # 0-based face indices within this element
        },
        ...
    ]
    """
    if types:
        where_type = "AND g.ifc_type = ANY(%s)"
        params = (types,)
    else:
        where_type = ""
        params = ()

    sql = f"""
        SELECT
            g.global_id,
            g.ifc_type,
            COALESCE(g.name, g.ifc_type)  AS name,
            (dp.path)[1]                   AS poly_idx,
            (dp.path)[3]                   AS pt_idx,
            ST_X(dp.geom)                  AS x,
            ST_Y(dp.geom)                  AS y,
            ST_Z(dp.geom)                  AS z
        FROM ifc_geometry g,
             LATERAL ST_DumpPoints(g.geom) dp
        WHERE g.geom IS NOT NULL
        {where_type}
        ORDER BY g.global_id, (dp.path)[1], (dp.path)[3];
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    print(f"  Fetched {len(rows):,} vertex rows from PostGIS")

    # ── Group rows into elements → triangles → vertices ───────────────────────
    elements   = {}          # global_id -> element dict
    cur_key    = None        # (global_id, poly_idx) of triangle being built
    cur_tri    = []          # vertices of current triangle

    def _flush(key, tri, elems):
        """Save completed triangle into its element."""
        if key is None or len(tri) < 3:
            return
        gid = key[0]
        elem  = elems[gid]
        base  = len(elem['verts'])
        elem['verts'].extend(tri[:3])
        elem['tris'].append((base, base + 1, base + 2))

    for global_id, ifc_type, name, poly_idx, pt_idx, x, y, z in rows:
        # Skip the 4th closing point of each ring (it repeats vertex 1)
        if pt_idx > 3:
            continue

        key = (global_id, poly_idx)

        # New triangle starting
        if key != cur_key:
            _flush(cur_key, cur_tri, elements)
            cur_key = key
            cur_tri = []

            # Register element on first encounter
            if global_id not in elements:
                elements[global_id] = {
                    'global_id': global_id,
                    'ifc_type':  ifc_type,
                    'name':      name,
                    'verts':     [],
                    'tris':      [],
                }

        cur_tri.append((x, y, z))

    _flush(cur_key, cur_tri, elements)   # flush the last triangle

    result = list(elements.values())
    print(f"  Elements reconstructed : {len(result)}")
    return result


# ── OBJ writer ────────────────────────────────────────────────────────────────

def write_obj(elements: list[dict], out_path: str):
    """Write all elements to a single OBJ + MTL file pair."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    mtl_path = out_path.replace('.obj', '.mtl')

    # MTL — one material per IFC type
    seen_types = {e['ifc_type'] for e in elements}
    with open(mtl_path, 'w') as mtl:
        for ifc_type in seen_types:
            r, g, b = TYPE_COLORS.get(ifc_type, TYPE_COLORS['_default'])
            mtl.write(f"newmtl {ifc_type}\n")
            mtl.write(f"Kd {r:.3f} {g:.3f} {b:.3f}\n")
            mtl.write(f"Ka 0.050 0.050 0.050\n")
            mtl.write(f"Ks 0.000 0.000 0.000\n\n")

    # OBJ — all vertices + faces, global index space
    total_tris    = 0
    vertex_offset = 1          # OBJ indices are 1-based
    current_type  = None

    with open(out_path, 'w') as obj:
        obj.write("# IFC Graph Twin — reconstructed from PostGIS\n")
        obj.write(f"# Elements : {len(elements)}\n")
        obj.write(f"mtllib {os.path.basename(mtl_path)}\n\n")

        for elem in elements:
            verts = elem['verts']
            tris  = elem['tris']
            if not verts or not tris:
                continue

            total_tris += len(tris)

            if elem['ifc_type'] != current_type:
                current_type = elem['ifc_type']
                obj.write(f"\nusemtl {current_type}\n")

            safe_name = str(elem['name']).replace(' ', '_').replace('/', '_')
            obj.write(f"o {elem['ifc_type']}_{safe_name}_{elem['global_id'][:8]}\n")

            for x, y, z in verts:
                obj.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

            for i0, i1, i2 in tris:
                obj.write(
                    f"f {vertex_offset+i0} {vertex_offset+i1} {vertex_offset+i2}\n"
                )

            vertex_offset += len(verts)

    print(f"  Triangles written : {total_tris:,}")
    print(f"  OBJ               : {out_path}")
    print(f"  MTL               : {mtl_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Reconstruct IFC 3D geometry from PostGIS -> OBJ'
    )
    parser.add_argument('--pg-dsn', default=PG_DSN,  help='PostGIS connection string')
    parser.add_argument('--out',    default=OUT_FILE, help='Output .obj path')
    parser.add_argument(
        '--types', nargs='*', default=DEFAULT_TYPES,
        help='IFC types to export (space-separated). Pass --types with no args for all.'
    )
    args = parser.parse_args()

    print(f"Connecting to PostGIS ...")
    try:
        conn = psycopg2.connect(args.pg_dsn)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    types = args.types if args.types else None
    if types:
        print(f"Exporting types: {', '.join(types)}")
    else:
        print("Exporting all types in ifc_geometry table")

    elements = fetch_geometry(conn, types)
    conn.close()

    if not elements:
        print("No geometry found. Run 02-all_relations.py first to populate PostGIS.")
        sys.exit(1)

    write_obj(elements, args.out)

    print(f"\nDone!")
    print(f"  Windows 3D Viewer : double-click {args.out}")
    print(f"  Blender           : File -> Import -> Wavefront (.obj)")


if __name__ == '__main__':
    main()
