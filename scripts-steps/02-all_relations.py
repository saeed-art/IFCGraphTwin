#!/usr/bin/env python3
"""Extract ALL IFC relationships generically and load into Neo4j.

Instead of hardcoding specific relation types, this script introspects
every IfcRelationship entity using the IFC naming convention:
  - Attributes starting with 'Relating' → the parent/source side
  - Attributes starting with 'Related'  → the child/target side (may be a list)

The Neo4j relationship type is derived from the IFC class name, e.g.:
  IfcRelAggregates     → AGGREGATES
  IfcRelContainedInSpatialStructure → CONTAINED_IN_SPATIAL_STRUCTURE

Usage:
  python scripts/all_relations.py
  python scripts/all_relations.py --ifc path/to/model.ifc --password secret
"""

import argparse
import re
import sys

import ifcopenshell
from neo4j import GraphDatabase

from geometry import make_settings, extract_geometry
from postgis_export import connect as pg_connect, setup_table, export_geometries

# ── Local defaults (edit as needed) ──────────────────────────────────────────
IFC_PATH     = "d:/Projects/01-IFC_structure/IFC_files/Simple/Duplex_A_20110907.ifc"
NEO4J_URI    = "bolt://localhost:7687"
NEO4J_USER   = "neo4j"
NEO4J_PASS   = "4262890Ab"
CLEAR_DB     = True
BATCH_SIZE   = 500   # nodes/rels per Neo4j transaction
PG_DSN       = "host=localhost port=5432 dbname=postgis_ifc user=postgres password=4262890"
# ─────────────────────────────────────────────────────────────────────────────


# ── IFC extraction ────────────────────────────────────────────────────────────

def ifc_class_to_rel_type(ifc_class: str) -> str:
    """Convert an IFC relationship class name to a Neo4j relationship type.

    IfcRelAggregates                    → AGGREGATES
    IfcRelContainedInSpatialStructure   → CONTAINED_IN_SPATIAL_STRUCTURE
    """
    # Strip leading 'IfcRel'
    name = re.sub(r'^IfcRel', '', ifc_class)
    # Insert underscore before each uppercase letter group, then upper-case all
    name = re.sub(r'([A-Z][a-z]+)', r'_\1', name).strip('_').upper()
    return name


def get_safe_name(entity) -> str:
    """Return a human-readable name for an IFC entity."""
    for attr in ('Name', 'LongName', 'Description'):
        val = getattr(entity, attr, None)
        if val and isinstance(val, str):
            return val
    return entity.is_a()


def extract(ifc_path: str):
    """Return (nodes dict, relationships list) from an IFC file.

    Nodes  : every entity that has a GlobalId
    Rels   : every IfcRelationship, discovered generically via get_info()
    """
    print(f"Opening IFC file: {ifc_path}")
    ifc = ifcopenshell.open(ifc_path)

    # ── 1. Collect nodes + geometry ───────────────────────────────────────────
    geom_settings = make_settings()          # create once, reuse for all entities
    nodes = {}
    geom_count = 0
    for entity in ifc:
        gid = getattr(entity, 'GlobalId', None)
        if not gid:
            continue
        # IfcRel* entities become edges — exclude them from the node set
        if entity.is_a('IfcRelationship'):
            continue
        geom = extract_geometry(entity, geom_settings)
        if geom:
            geom_count += 1
        nodes[gid] = {
            'globalId': gid,
            'ifcType':  entity.is_a(),
            'name':     get_safe_name(entity),
            'geometry': geom,            # dict with vertices/faces, or None
        }
    print(f"  Entities with GlobalId : {len(nodes)}")
    print(f"  Entities with geometry : {geom_count}")

    # ── 2. Discover relationships via IfcRelationship entities ────────────────
    seen = set()
    relationships = []

    for rel in ifc.by_type('IfcRelationship'):
        rel_type = ifc_class_to_rel_type(rel.is_a())
        info = rel.get_info()

        # Find the 'Relating' (source) and 'Related' (targets) attributes
        relating_obj = None
        related_objs = []

        for key, value in info.items():
            # Skip metadata attributes
            if key in ('id', 'type', 'GlobalId', 'OwnerHistory', 'Name', 'Description'):
                continue

            if key.startswith('Relating'):
                if hasattr(value, 'GlobalId') and value.GlobalId in nodes:
                    relating_obj = value

            elif key.startswith('Related'):
                # Can be a single entity or a collection
                if isinstance(value, (list, tuple)):
                    for v in value:
                        if hasattr(v, 'GlobalId') and v.GlobalId in nodes:
                            related_objs.append(v)
                elif hasattr(value, 'GlobalId') and value.GlobalId in nodes:
                    related_objs.append(value)

        if relating_obj is None:
            continue

        for target in related_objs:
            key = (relating_obj.GlobalId, rel_type, target.GlobalId)
            if key not in seen:
                seen.add(key)
                relationships.append({
                    'start':        relating_obj.GlobalId,
                    'rel_type':     rel_type,
                    'end':          target.GlobalId,
                })

    print(f"  Via IfcRelationship    : {len(relationships)}")

    # ── 3. Direct attribute-based connections (non-Rel edges) ─────────────────
    # Some IFC connections use plain attributes instead of IfcRel* objects,
    # e.g. IfcWindowStyle.HasPropertySets → [IfcWindowLiningProperties].
    #
    # Strategy: use the IFC schema to get only FORWARD attributes for each
    # entity type. The schema distinguishes forward attributes (stored on the
    # entity) from inverse attributes (back-references computed at query time).
    # Inverse attributes would duplicate the edges already captured in Pass 2,
    # so we skip them — no hardcoding needed.

    ifc_schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(ifc.schema)
    _forward_attr_cache: dict = {}

    def forward_attrs(entity_type: str) -> frozenset:
        """Return the forward attribute names for an IFC entity type (cached)."""
        if entity_type not in _forward_attr_cache:
            try:
                decl = ifc_schema.declaration_by_name(entity_type)
                _forward_attr_cache[entity_type] = frozenset(
                    a.name() for a in decl.all_attributes()
                )
            except Exception:
                _forward_attr_cache[entity_type] = frozenset()
        return _forward_attr_cache[entity_type]

    def attr_to_rel_type(attr_name: str) -> str:
        """CamelCase attribute name → SNAKE_UPPER relationship type."""
        s = re.sub(r'([A-Z][a-z]+)', r'_\1', attr_name).strip('_').upper()
        return s

    direct_count = 0
    for entity in ifc:
        src_gid = getattr(entity, 'GlobalId', None)
        if not src_gid or src_gid not in nodes:
            continue

        allowed = forward_attrs(entity.is_a())
        info = entity.get_info()

        for attr, value in info.items():
            # Only process schema-declared forward attributes;
            # 'id' and 'type' are get_info() internals, not in the schema.
            if attr not in allowed:
                continue

            targets = []
            if isinstance(value, (list, tuple)):
                targets = [v for v in value if hasattr(v, 'GlobalId') and v.GlobalId in nodes]
            elif hasattr(value, 'GlobalId') and value.GlobalId in nodes:
                targets = [value]

            if not targets:
                continue

            rel_type = attr_to_rel_type(attr)
            for tgt in targets:
                edge_key = (src_gid, rel_type, tgt.GlobalId)
                if edge_key not in seen:
                    seen.add(edge_key)
                    relationships.append({
                        'start':    src_gid,
                        'rel_type': rel_type,
                        'end':      tgt.GlobalId,
                    })
                    direct_count += 1

    print(f"  Via direct attributes  : {direct_count}")
    print(f"  Total relationships    : {len(relationships)}")
    return nodes, relationships


# ── Neo4j loading ─────────────────────────────────────────────────────────────

def load(uri: str, user: str, password: str,
         nodes: dict, relationships: list, clear: bool = True):
    """Write nodes and relationships to Neo4j in batches."""
    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session() as session:

        if clear:
            print("Clearing existing graph …")
            session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))

        # ── Write nodes in batches ────────────────────────────────────────────
        node_list = list(nodes.values())
        print(f"Writing {len(node_list)} nodes …")
        for i in range(0, len(node_list), BATCH_SIZE):
            batch = node_list[i:i + BATCH_SIZE]
            session.execute_write(_write_nodes, batch)

        # ── Write relationships in batches ────────────────────────────────────
        print(f"Writing {len(relationships)} relationships …")
        for i in range(0, len(relationships), BATCH_SIZE):
            batch = relationships[i:i + BATCH_SIZE]
            session.execute_write(_write_rels, batch)

    driver.close()
    print("Done.")


def _write_nodes(tx, batch: list):
    """MERGE a batch of nodes with derived geometric properties.

    Stores compact, queryable properties in Neo4j.
    Raw vertices/faces stay in PostGIS only — not written here.
    """
    for n in batch:
        label = n['ifcType']
        geom  = n.get('geometry')   # dict with derived props, or None
        tx.run(
            f"MERGE (x:{label} {{globalId:$gid}}) "
            "SET x.name=$name, x.ifcType=$ifcType, "
            "x.centroid=$centroid, x.boundingBox=$bounding_box, "
            "x.floorLevel=$floor_level, "
            "x.volume=$volume, x.surfaceArea=$surface_area",
            gid=n['globalId'],
            name=n['name'],
            ifcType=n['ifcType'],
            centroid=     geom['centroid']     if geom else None,
            bounding_box= geom['bounding_box'] if geom else None,
            floor_level=  geom['floor_level']  if geom else None,
            volume=       geom['volume']        if geom else None,
            surface_area= geom['surface_area']  if geom else None,
        )


def _write_rels(tx, batch: list):
    """MERGE a batch of relationships."""
    for r in batch:
        tx.run(
            "MATCH (a {globalId:$start}), (b {globalId:$end}) "
            f"MERGE (a)-[:{r['rel_type']}]->(b)",
            start=r['start'], end=r['end']
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Extract ALL IFC relationships generically and load into Neo4j + PostGIS'
    )
    parser.add_argument('--ifc',      default=IFC_PATH,    help='Path to IFC file')
    parser.add_argument('--uri',      default=NEO4J_URI,   help='Neo4j bolt URI')
    parser.add_argument('--user',     default=NEO4J_USER,  help='Neo4j username')
    parser.add_argument('--password', default=NEO4J_PASS,  help='Neo4j password')
    parser.add_argument('--no-clear', action='store_true',  help='Skip clearing Neo4j DB')
    parser.add_argument('--pg-dsn',   default=PG_DSN,
                        help='PostGIS DSN (set to empty string to skip PostGIS export)')
    args = parser.parse_args()

    try:
        nodes, rels = extract(args.ifc)
    except Exception as e:
        print(f"Error reading IFC file: {e}")
        sys.exit(1)

    # ── Neo4j ───────────────────────────────────────────────────────────────────
    load(args.uri, args.user, args.password,
         nodes, rels, clear=not args.no_clear)

    # ── PostGIS ────────────────────────────────────────────────────────────
    if args.pg_dsn:
        print("\nExporting geometry to PostGIS …")
        try:
            pg_conn = pg_connect(args.pg_dsn)
            setup_table(pg_conn)
            export_geometries(pg_conn, nodes)
            pg_conn.close()
        except Exception as e:
            print(f"  PostGIS export failed: {e}")
            print("  (Skipping — Neo4j data is unaffected)")
    else:
        print("\nPostGIS export skipped (--pg-dsn is empty).")

    print("\nView your graph in Neo4j Browser (http://localhost:7474):")
    print("  MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 200")


if __name__ == '__main__':
    main()
